"""Launcher checks use fake subprocesses and local HTTP; never LinkedIn."""

import asyncio
import fcntl
import os
import socket
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from linkedin_dashboard.launcher import (
    CompassFiles,
    ManagedConnector,
    ensure_connector,
    launcher_lock,
    require_free_port,
    supported_node,
)


def test_spa_routes_do_not_mask_missing_apis_or_assets(tmp_path):
    (tmp_path / "index.html").write_text("<h1>Compass fixture</h1>")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets/app.js").write_text("// local fixture")
    app = FastAPI()
    app.mount("/", CompassFiles(directory=tmp_path))
    with TestClient(app) as client:
        for route in (
            "/",
            "/brief",
            "/settings",
            "/candidates/person",
            "/how-it-works/after-a-request",
        ):
            response = client.get(route)
            assert response.status_code == 200
            assert "Compass fixture" in response.text
            assert response.headers["cache-control"] == "no-cache"
        assert client.get("/assets/app.js").status_code == 200
        for route in ("/api/missing", "/assets/missing.js", "/.env", "/unknown"):
            assert client.get(route).status_code == 404


def test_occupied_ports_are_rejected_without_touching_listener():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        with pytest.raises(RuntimeError, match="already in use"):
            require_free_port(port)
        assert listener.fileno() >= 0


@pytest.mark.parametrize(
    "version, supported",
    [
        ("v20.18.0", False),
        ("v20.19.0", True),
        ("v22.11.0", False),
        ("v22.22.0", True),
        ("v24.0.0", True),
    ],
)
def test_node_compatibility(version, supported):
    with patch("subprocess.check_output", return_value=version):
        assert supported_node("node") is supported


def test_connector_install_is_cached_and_failed_setup_is_retryable(tmp_path):
    root, cache = tmp_path / "root", tmp_path / "cache"
    patches = root / "integrations/linkedin-mcp-server"
    patches.mkdir(parents=True)
    cache.mkdir()
    for name in ("people-pagination.patch", "parallel-profiles.patch"):
        (patches / name).write_text(name)
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[1] == "clone":
            Path(command[-1]).mkdir()

    with patch("linkedin_dashboard.launcher.run", side_effect=fake_run):
        first = ensure_connector(root, cache, "uv")
        count = len(commands)
        assert ensure_connector(root, cache, "uv") == first
        assert len(commands) == count
        assert sum(cmd[:3] == ["git", "apply", "--check"] for cmd in commands) == 2
        (patches / "people-pagination.patch").write_text("new patch")
        second = ensure_connector(root, cache, "uv")
        assert second != first
        assert first.exists()  # Earlier working installation remains recoverable.


@pytest.mark.asyncio
async def test_manager_login_failure_retry_and_shutdown_are_owned(tmp_path):
    script = tmp_path / "connector.py"
    script.write_text("""
import pathlib, socket, sys, time
root = pathlib.Path(__file__).parent
if '--login' in sys.argv:
    if not (root / 'allow-login').exists():
        sys.exit(1)
    (root / 'did-login').write_text('yes')
    sys.exit(0)
port = int(sys.argv[sys.argv.index('--port') + 1])
with socket.socket() as server:
    server.bind(('127.0.0.1', port))
    server.listen()
    time.sleep(60)
""")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    manager = ManagedConnector(
        "uv", tmp_path, tmp_path / "profile", port, tmp_path / "connector.log"
    )
    manager.command = [sys.executable, str(script)]
    manager.begin()
    assert manager.task is not None
    await asyncio.wait_for(manager.task, timeout=5)
    assert manager.phase == "login_failed"
    (tmp_path / "allow-login").touch()
    manager.begin(login=True)
    try:
        for _ in range(50):
            if manager.phase == "ready":
                break
            await asyncio.sleep(0.1)
        assert manager.phase == "ready"
        assert (tmp_path / "did-login").exists()
        assert manager.process is not None and manager.process.returncode is None
    finally:
        await manager.close()
    assert manager.process is not None and manager.process.returncode is not None
    require_free_port(port)


def test_managed_app_keeps_queue_stopped_during_login_and_guards_retry(tmp_path):
    from linkedin_dashboard.launcher import managed_app
    from linkedin_dashboard.settings import Settings

    manager = ManagedConnector(
        "uv", tmp_path, tmp_path / "profile", 8000, tmp_path / "log"
    )
    manager.phase = "login_failed"
    (tmp_path / "index.html").write_text("Compass fixture")
    with patch.object(manager, "begin") as begin:
        app = managed_app(Settings(db_path=tmp_path / "app.db"), manager, tmp_path)
        with patch.object(app.state.job_queue, "start") as start:
            with TestClient(app, base_url="http://127.0.0.1") as client:
                assert client.get("/brief").status_code == 200
                assert client.get("/api/launcher").json()["phase"] == "login_failed"
                for phase in (
                    "starting",
                    "signing_in",
                    "connecting",
                    "login_failed",
                    "failed",
                ):
                    manager.phase = phase
                    response = client.get("/api/mcp/status")
                    assert response.status_code == 200
                    assert response.json()["reachable"] is False
                    assert response.json()["last_error_class"] is None
                    assert client.get("/api/jobs").json() == []
                    assert client.get("/api/session").status_code == 200
                manager.phase = "login_failed"
                start.assert_not_called()
                assert (
                    client.post(
                        "/api/launcher/login",
                        headers={"Origin": "https://external.example"},
                    ).status_code
                    == 403
                )
                assert client.post("/api/launcher/login").status_code == 202
                begin.assert_called_with(login=True)
                manager.phase = "signing_in"
                assert client.post("/api/launcher/login").status_code == 409


def test_status_probe_starts_only_after_managed_connector_is_ready(tmp_path):
    from linkedin_dashboard.main import create_app
    from linkedin_dashboard.queue.jobs import JobKind
    from linkedin_dashboard.settings import Settings

    calls = []

    class StatusExecutor:
        async def execute(self, payload, capture_raw, report_progress):
            calls.append(payload.kind)
            raw = {"tools": [{"name": "search_people"}]}
            await capture_raw(raw, None)
            return raw

    ready = False
    app = create_app(
        Settings(db_path=tmp_path / "status.db"),
        queue_executor=StatusExecutor(),
        retrieval_ready=lambda: ready,
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/api/mcp/status").json()["reachable"] is False
        assert calls == []
        assert client.get("/api/jobs").json() == []
        ready = True
        response = client.get("/api/mcp/status")
        assert response.status_code == 200
        assert response.json()["reachable"] is True
        assert response.json()["tools"] == ["search_people"]
        assert calls == [JobKind.LIST_TOOLS]


@pytest.fixture
def running_launcher(tmp_path):
    processes = []

    def start(
        *, state="running", module="linkedin_dashboard.launcher", other_root=False
    ):
        root = tmp_path / "repo"
        cache = root / ".compass"
        cache.mkdir(parents=True, exist_ok=True)
        launch_root = tmp_path / "other" if other_root else root
        package = launch_root / "linkedin_dashboard"
        package.mkdir(parents=True, exist_ok=True)
        (package / "__init__.py").write_text("")
        script = (
            package / "launcher.py"
            if module.startswith("linkedin_dashboard")
            else launch_root / "other.py"
        )
        script.write_text("""
import fcntl, pathlib, signal, subprocess, sys, time
cache = pathlib.Path(sys.argv[1])
with (cache / 'launcher.lock').open('w') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    lock.write(sys.argv[2])
    lock.flush()
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    def stop(*args):
        child.terminate()
        child.wait(timeout=5)
        (cache / 'closed-cleanly').write_text('yes')
        sys.exit(0)
    signal.signal(signal.SIGTERM, stop)
    print('ready', flush=True)
    while True:
        time.sleep(.05)
""")
        process = subprocess.Popen(
            [sys.executable, "-m", module, str(cache), state],
            cwd=launch_root,
            env={**os.environ, "PYTHONPATH": str(launch_root)},
            stdout=subprocess.PIPE,
            text=True,
        )
        processes.append(process)
        assert process.stdout.readline().strip() == "ready"
        return root, cache, process

    yield start
    for process in processes:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


@pytest.mark.parametrize("state", ["", "running"])
def test_repeat_launch_stops_verified_owner_and_waits_for_cleanup(
    running_launcher, state
):
    root, cache, process = running_launcher(state=state)
    saved = root / "saved-data"
    saved.write_text("keep this")
    with launcher_lock(cache, root):
        assert process.poll() is not None
        assert (cache / "closed-cleanly").read_text() == "yes"
        assert saved.read_text() == "keep this"
        assert (cache / "launcher.lock").read_text() == "preparing"
    # A leftover lock file does not block later launches.
    with launcher_lock(cache, root):
        pass


@pytest.mark.parametrize("other_root", [False, True])
def test_repeat_launch_refuses_unrelated_lock_owner(running_launcher, other_root):
    root, cache, process = running_launcher(
        module="linkedin_dashboard.launcher" if other_root else "other",
        other_root=other_root,
    )
    with pytest.raises(RuntimeError, match="Cannot verify"):
        with launcher_lock(cache, root):
            pytest.fail("Unrelated process must retain its lock")
    assert process.poll() is None


def test_repeat_launch_does_not_interrupt_installation(running_launcher):
    root, cache, process = running_launcher(state="preparing")
    with pytest.raises(RuntimeError, match="still preparing"):
        with launcher_lock(cache, root):
            pytest.fail("Installation must finish first")
    assert process.poll() is None


def test_setup_only_does_not_stop_running_app(running_launcher):
    root, cache, process = running_launcher()
    with pytest.raises(RuntimeError, match="setup-only"):
        with launcher_lock(cache, root, setup_only=True):
            pytest.fail("Maintenance must not stop the app")
    assert process.poll() is None


def test_simultaneous_restart_is_serialized(tmp_path):
    with (tmp_path / "launcher-restart.lock").open("a+") as takeover:
        fcntl.flock(takeover, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match="Another Compass launch"):
            with launcher_lock(tmp_path, tmp_path):
                pytest.fail("Only one takeover can run")
