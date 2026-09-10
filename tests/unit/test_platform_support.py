"""Real local locks/processes and native Windows entry-point coverage; no login."""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from unittest.mock import patch

import psutil
import pytest
from linkedin_dashboard.db.session import Database
from linkedin_dashboard.instance import (
    OwnerRecord,
    own_record,
    take_shutdown_request,
    verify_owner,
)
from linkedin_dashboard.launcher import (
    NodeRuntime,
    extract_archive,
    launcher_lock,
    node_download,
    prepare_frontend,
)
from linkedin_dashboard.platform_support import IS_WINDOWS
from linkedin_dashboard.platform_support.locks import LockUnavailable, StateLock
from linkedin_dashboard.platform_support.privacy import (
    create_private_directories,
    require_private_directory,
)
from linkedin_dashboard.platform_support.processes import spawn_contained

PROJECT = Path(__file__).resolve().parents[2]


def test_lock_excludes_other_handles_and_processes_but_state_stays_readable(tmp_path):
    path = tmp_path / "lock"
    with StateLock(path) as owner, StateLock(path) as other:
        owner.acquire()
        owner.write_state("preparing")
        assert other.read_state() == "preparing"
        with pytest.raises(LockUnavailable):
            other.acquire()
        script = """
import sys
from pathlib import Path
from linkedin_dashboard.platform_support.locks import StateLock, LockUnavailable
with StateLock(Path(sys.argv[1])) as lock:
    assert lock.read_state() == 'preparing'
    try:
        lock.acquire()
    except LockUnavailable:
        print('blocked')
    else:
        raise AssertionError('second process acquired a held lock')
"""
        result = subprocess.run(
            [sys.executable, "-c", script, str(path)],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        assert result.stdout.strip() == "blocked"
        owner.write_state("running")
        assert other.read_state() == "running"
    with StateLock(path) as restarted:
        restarted.acquire()


def test_cooperative_restart_runs_cleanup_before_taking_lock(tmp_path):
    root = tmp_path / "Compass Jäne with spaces"
    package = root / "linkedin_dashboard"
    cache = root / ".compass"
    package.mkdir(parents=True)
    cache.mkdir()
    real_package = PROJECT / "backend/linkedin_dashboard"
    (package / "__init__.py").write_text(
        f"__path__.append({str(real_package)!r})",
        encoding="utf-8",
    )
    (package / "launcher.py").write_text(
        """
import pathlib, sys, time
from linkedin_dashboard.instance import own_record, take_shutdown_request
from linkedin_dashboard.platform_support.locks import StateLock
root = pathlib.Path.cwd()
cache = root / '.compass'
record = own_record('running', root)
with StateLock(cache / 'launcher.lock') as lock:
    lock.acquire()
    lock.write_state(record.serialize())
    print('ready', flush=True)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if take_shutdown_request(cache, record):
            (cache / 'closed-cleanly').write_text('yes')
            break
        time.sleep(.02)
""",
        encoding="utf-8",
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "linkedin_dashboard.launcher"],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root), "PYTHONUTF8": "1"},
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "ready"
        with launcher_lock(cache, root) as owner:
            assert process.poll() is not None
            assert (cache / "closed-cleanly").read_text() == "yes"
            assert "preparing" in owner.read_state()
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=10)


@pytest.mark.parametrize("bad_time", ["bad", None, {}, float("nan"), float("inf")])
def test_malformed_shutdown_request_does_not_crash_watcher(tmp_path, bad_time):
    record = own_record("running", tmp_path)
    (tmp_path / "launcher-shutdown.json").write_text(
        json.dumps({"pid": record.pid, "create_time": bad_time}),
        encoding="utf-8",
    )
    assert not take_shutdown_request(tmp_path, record)


def test_owner_identity_requires_actual_repository_directory(tmp_path):
    record = own_record("running", tmp_path)
    with patch(
        "psutil.Process.cmdline",
        return_value=["python", "-m", "linkedin_dashboard.launcher"],
    ):
        assert verify_owner(record, tmp_path) is None
    assert OwnerRecord.parse(record.serialize()) == record
    assert (
        OwnerRecord.parse(record.serialize().replace(str(record.create_time), '"nan"'))
        is None
    )


@pytest.mark.parametrize("machine", ["AMD64", "ARM64"])
def test_windows_node_archives(machine):
    target = node_download("Windows", machine)
    assert target is not None
    assert target[1].endswith(".zip")
    assert "win-" in target[0]


@pytest.mark.parametrize(
    "member",
    ["../escape", "node/../../escape", "C:/escape", "node/evil:stream", "node\\escape"],
)
def test_node_zip_rejects_escaping_paths(tmp_path, member):
    archive = tmp_path / "node.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(member, "no")
    with pytest.raises(RuntimeError, match="unexpected path"):
        extract_archive(archive, tmp_path / "out", expected_root="node")


def test_frontend_uses_node_directly_and_caches_successful_build(tmp_path):
    frontend, cache = tmp_path / "frontend", tmp_path / "cache"
    frontend.mkdir()
    cache.mkdir()
    for name in ("package.json", "package-lock.json"):
        (frontend / name).write_text("{}")
    runtime = NodeRuntime(
        tmp_path / "Jäne & Node/node.exe", tmp_path / "npm/npm-cli.js"
    )
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        assert kwargs["cwd"] == frontend
        if "ci" in command:
            (frontend / "node_modules").mkdir(exist_ok=True)
        if command[-2:] == ["run", "build"]:
            (frontend / "dist").mkdir(exist_ok=True)
            (frontend / "dist/index.html").write_text("ready")

    with (
        patch("linkedin_dashboard.launcher.ensure_node", return_value=runtime),
        patch(
            "linkedin_dashboard.launcher.run",
            side_effect=run,
        ),
    ):
        assert prepare_frontend(tmp_path, cache) == frontend / "dist"
        prepare_frontend(tmp_path, cache)
    assert len(commands) == 2
    assert all(
        command[:2] == [str(runtime.node), str(runtime.npm_cli)] for command in commands
    )


@pytest.mark.asyncio
async def test_contained_process_stops_descendants_and_preserves_unrelated_process(
    tmp_path,
):
    child_pid = tmp_path / "child.pid"
    script = """
import pathlib, subprocess, sys, time
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
pathlib.Path(sys.argv[1]).write_text(str(child.pid))
time.sleep(60)
"""
    unrelated = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(60)"
    )
    contained = None
    try:
        with (tmp_path / "log").open("wb") as output:
            contained = await spawn_contained(
                [sys.executable, "-c", script, str(child_pid)],
                cwd=tmp_path,
                output=output.fileno(),
                containment_dir=tmp_path,
            )
        for _ in range(200):
            if child_pid.exists():
                break
            await asyncio.sleep(0.025)
        assert child_pid.exists()
        child = psutil.Process(int(child_pid.read_text()))
        await contained.stop(grace=2)
        for _ in range(100):
            if not child.is_running() or child.status() == psutil.STATUS_ZOMBIE:
                break
            await asyncio.sleep(0.025)
        assert not child.is_running() or child.status() == psutil.STATUS_ZOMBIE
        assert unrelated.returncode is None
    finally:
        if contained:
            await contained.stop(grace=1)
        unrelated.terminate()
        await asyncio.wait_for(unrelated.wait(), timeout=10)


def test_private_database_directory(tmp_path):
    directory = tmp_path / "new" / "private"
    create_private_directories(directory)
    require_private_directory(directory)


@pytest.mark.skipif(not IS_WINDOWS, reason="Windows Job Objects retain descendants")
@pytest.mark.asyncio
async def test_windows_cleanup_after_intermediate_process_exits(tmp_path):
    pid_file = tmp_path / "orphan.pid"
    script = """
import pathlib, subprocess, sys
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
pathlib.Path(sys.argv[1]).write_text(str(child.pid))
"""
    with (tmp_path / "log").open("wb") as output:
        process = await spawn_contained(
            [sys.executable, "-c", script, str(pid_file)],
            cwd=tmp_path,
            output=output.fileno(),
            containment_dir=tmp_path,
        )
    try:
        assert await asyncio.wait_for(process.wait(), timeout=10) == 0
        child = psutil.Process(int(pid_file.read_text()))
        assert child.is_running()
        await process.stop(grace=0.2)
        for _ in range(100):
            if not child.is_running():
                break
            await asyncio.sleep(0.025)
        assert not child.is_running()
    finally:
        await process.stop(grace=0.2)


def test_database_initialization_and_exclusive_queue_owner(tmp_path):
    database = Database(tmp_path / "private" / "session.db")
    descriptor = None
    try:
        database.initialize()
        descriptor = database.acquire_worker_lock()
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                """
import sys
from pathlib import Path
from linkedin_dashboard.db.session import Database
database = Database(Path(sys.argv[1]))
try:
    try:
        descriptor = database.acquire_worker_lock()
    except BlockingIOError:
        print('blocked')
    else:
        database.release_worker_lock(descriptor)
        raise AssertionError('second queue owner was accepted')
finally:
    database.dispose()
""",
                str(database.path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.stdout.strip() == "blocked"
    finally:
        if descriptor is not None:
            database.release_worker_lock(descriptor)
        database.dispose()


@pytest.mark.skipif(not IS_WINDOWS, reason="Native NTFS ACL behavior")
def test_windows_repairs_explicit_public_file_acl(tmp_path):
    from linkedin_dashboard.platform_support import windows_acl
    from linkedin_dashboard.platform_support.privacy import (
        open_owner_only_file,
        windows_unauthorized_trustees,
    )

    directory = tmp_path / "private"
    create_private_directories(directory)
    path = directory / "existing.db"
    path.write_bytes(b"private data")
    security = windows_acl.security
    sid = windows_acl.current_user_sid()
    descriptor = security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        f"D:P(A;;FA;;;{sid})(A;;FR;;;WD)",
        security.SDDL_REVISION_1,
    )
    security.SetNamedSecurityInfo(
        str(path),
        security.SE_FILE_OBJECT,
        security.DACL_SECURITY_INFORMATION
        | security.PROTECTED_DACL_SECURITY_INFORMATION,
        None,
        None,
        descriptor.GetSecurityDescriptorDacl(),
        None,
    )
    handle = open_owner_only_file(path, create=False)
    os.close(handle)
    _, entries = windows_acl.describe_access(path)
    assert not windows_unauthorized_trustees(entries, sid)
    assert path.read_bytes() == b"private data"


@pytest.mark.skipif(not IS_WINDOWS, reason="Executes native Windows command resolution")
@pytest.mark.parametrize("command", [r".\compass", "./compass"])
def test_windows_one_command_forwards_flags_and_exit_status(tmp_path, command):
    root = tmp_path / "Compass Jäne & spaces"
    (root / "scripts").mkdir(parents=True)
    tools = root / ".compass/tools"
    tools.mkdir(parents=True)
    for name in ("compass", "compass.cmd", "scripts/compass-windows.ps1"):
        shutil.copyfile(PROJECT / name, root / name)
    # A tiny native fixture avoids downloading tools while exercising the real
    # .cmd -> PowerShell -> uv.exe argument and exit-code path.
    compiler = tmp_path / "compile.ps1"
    compiler.write_text(
        """
param([string]$Target)
Add-Type -OutputAssembly $Target -OutputType ConsoleApplication -TypeDefinition @'
using System;
using System.IO;
public class Program {
  public static int Main(string[] args) {
    File.WriteAllLines(Environment.GetEnvironmentVariable("COMPASS_TEST_ARGS"), args);
    return 7;
  }
}
'@
""",
        encoding="utf-8",
    )
    subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(compiler),
            str(tools / "uv.exe"),
        ],
        check=True,
        timeout=60,
    )
    git = shutil.which("git.exe")
    assert git is not None
    windows = os.environ["SystemRoot"]
    env = {**os.environ, "COMPASS_TEST_ARGS": str(tmp_path / "args.txt")}
    env["PATH"] = os.pathsep.join(
        [
            str(Path(git).parent),
            str(Path(windows) / "System32"),
            str(Path(windows) / "System32/WindowsPowerShell/v1.0"),
        ]
    )
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            f"& '{command}' --port 8899 --no-open; exit $LASTEXITCODE",
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 7, result.stdout + result.stderr
    args = (tmp_path / "args.txt").read_text(encoding="utf-8-sig").splitlines()
    assert args[-3:] == ["--port", "8899", "--no-open"]
    assert args[:3] == ["run", "--frozen", "--no-dev"]
