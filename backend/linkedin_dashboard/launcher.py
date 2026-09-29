"""One-command local setup and owned-process lifecycle. Never performs a search."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import os
import platform
import shutil
import socket
import stat
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import webbrowser
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import uvicorn
from fastapi import HTTPException
from fastapi.responses import FileResponse
from starlette.staticfiles import StaticFiles

from linkedin_dashboard.instance import (
    REQUEST_POLL_SECONDS,
    RUNNING,
    OwnerRecord,
    clear_shutdown_request,
    launcher_lock,
    own_record,
    take_shutdown_request,
)
from linkedin_dashboard.main import create_app
from linkedin_dashboard.platform_support import BOOTSTRAP_COMMAND, IS_WINDOWS
from linkedin_dashboard.platform_support.privacy import (
    create_private_directories,
    remove_tree,
)
from linkedin_dashboard.platform_support.processes import (
    ContainedProcess,
    spawn_contained,
)
from linkedin_dashboard.settings import PROJECT_ROOT, Settings

UPSTREAM = "https://github.com/stickerdaniel/linkedin-mcp-server.git"
CONNECTOR_REVISION = "f410bfdc32569f8763fde11338b24ec6a0797f0d"
NODE_VERSION = "22.22.0"
PATCHES = ("people-pagination.patch", "parallel-profiles.patch")
DOWNLOAD_ATTEMPTS = 3
RETRY_SECONDS = 2.0
# Git checks out and applies patches byte for byte here: Windows line-ending
# translation would rewrite the bundled patches and stop them applying.
GIT_TEXT_SETTINGS = (
    "-c",
    "core.autocrlf=false",
    "-c",
    "core.eol=lf",
    "-c",
    "core.safecrlf=false",
    "-c",
    "core.longpaths=true",
)


def run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
    subprocess.run(command, cwd=cwd, env=env, check=True)


def git(arguments: list[str], *, cwd: Path) -> None:
    """Run Git with the text handling Compass's bundled patches require."""
    run(["git", *GIT_TEXT_SETTINGS, *arguments], cwd=cwd)


def fingerprint(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(str(path).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def ensure_connector(root: Path, cache: Path, uv: str) -> Path:
    patches = [root / "integrations/linkedin-mcp-server" / name for name in PATCHES]
    version = hashlib.sha256(
        (CONNECTOR_REVISION + fingerprint(patches)).encode()
    ).hexdigest()[:16]
    destination = cache / f"connector-{version}"
    marker = destination / ".compass-ready"
    if not marker.exists():
        print("Preparing LinkedIn support…", flush=True)
        # Publish only complete installations; never patch a user's checkout.
        temporary = Path(tempfile.mkdtemp(prefix="connector-", dir=cache))
        try:
            checkout = temporary / "source"
            git(
                ["clone", "--quiet", "--no-checkout", UPSTREAM, str(checkout)],
                cwd=root,
            )
            git(["checkout", "--quiet", "--detach", CONNECTOR_REVISION], cwd=checkout)
            for patch in patches:
                git(["apply", "--check", str(patch)], cwd=checkout)
                git(["apply", str(patch)], cwd=checkout)
            # Move before installing: virtual-environment scripts embed absolute paths.
            if destination.exists():
                remove_tree(destination)
            checkout.rename(destination)
        finally:
            # Git leaves object files read-only, which Windows refuses to delete
            # through a plain tree removal.
            remove_tree(temporary)
        run([uv, "sync", "--frozen", "--no-dev", "--python", "3.13"], cwd=destination)
        marker.write_text(version, encoding="utf-8")
    return destination


def supported_node(executable: str | Path) -> bool:
    try:
        version = subprocess.check_output(
            [str(executable), "--version"], text=True
        ).strip()
        major, minor, *_ = map(int, version.lstrip("v").split("."))
        return (
            (major == 20 and minor >= 19)
            or (major == 22 and minor >= 12)
            or major >= 24
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


@dataclass(frozen=True)
class NodeRuntime:
    """A Node.js executable and npm's own entry point.

    npm ships as ``npm.cmd`` on Windows, which is a batch file: running it means
    handing a user's folder names to the command interpreter to re-parse.
    Compass runs npm's JavaScript through Node directly instead, so paths with
    spaces, ampersands or non-ASCII characters are passed as arguments and never
    parsed as commands.
    """

    node: Path
    npm_cli: Path

    @property
    def bin_directory(self) -> Path:
        return self.node.parent

    def npm(self, *arguments: str) -> list[str]:
        return [str(self.node), str(self.npm_cli), *arguments]

    def environment(self) -> dict[str, str]:
        return {
            **os.environ,
            "PATH": str(self.bin_directory) + os.pathsep + os.environ.get("PATH", ""),
        }


def npm_cli_for(node: Path, *, search_path: bool = False) -> Path | None:
    """Find npm's CLI script for a Node.js executable, on any layout."""
    directory = node.parent
    candidates = [
        directory / "node_modules/npm/bin/npm-cli.js",  # Windows distributions
        directory.parent / "lib/node_modules/npm/bin/npm-cli.js",  # POSIX layout
    ]
    npm = shutil.which("npm", path=str(directory))
    if not npm and search_path:
        npm = shutil.which("npm")
    if npm:
        resolved = Path(npm).resolve()
        candidates += [resolved, resolved.parent / "node_modules/npm/bin/npm-cli.js"]
    for candidate in candidates:
        if candidate.name == "npm-cli.js" and candidate.is_file():
            return candidate
    return None


def node_download(system: str, machine: str) -> tuple[str, str, str] | None:
    """Return the ``(name, archive, extension)`` Node publishes for this machine."""
    platform_name = {"Darwin": "darwin", "Linux": "linux", "Windows": "win"}.get(system)
    architecture = {
        "arm64": "arm64",
        "aarch64": "arm64",
        "ARM64": "arm64",
        "x86_64": "x64",
        "AMD64": "x64",
    }.get(machine)
    if not platform_name or not architecture:
        return None
    name = f"node-v{NODE_VERSION}-{platform_name}-{architecture}"
    extension = "zip" if platform_name == "win" else "tar.gz"
    return name, f"{name}.{extension}", extension


def node_runtime_at(directory: Path) -> NodeRuntime | None:
    """Describe an extracted Node.js distribution, whatever its layout."""
    for node in (directory / "node.exe", directory / "bin/node"):
        if node.exists():
            npm_cli = npm_cli_for(node)
            if npm_cli:
                return NodeRuntime(node=node, npm_cli=npm_cli)
    return None


def fetch(url: str, *, timeout: float) -> bytes:
    """Download one small file, retrying briefly around a flaky connection."""
    last: Exception | None = None
    for attempt in range(DOWNLOAD_ATTEMPTS):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                return bytes(response.read())
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last = error
            if attempt + 1 < DOWNLOAD_ATTEMPTS:
                time.sleep(RETRY_SECONDS)
    raise RuntimeError(f"Could not download {url}: {last}")


def download_verified(url: str, destination: Path, digest: str, *, timeout: float):
    """Stream a download to disk and keep it only if it matches its checksum."""
    last: Exception | None = None
    for attempt in range(DOWNLOAD_ATTEMPTS):
        try:
            checksum = hashlib.sha256()
            with urllib.request.urlopen(url, timeout=timeout) as response:
                with destination.open("wb") as output:
                    while chunk := response.read(1 << 20):
                        checksum.update(chunk)
                        output.write(chunk)
            if checksum.hexdigest() == digest:
                return
            last = RuntimeError("checksum did not match")
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last = error
        destination.unlink(missing_ok=True)
        if attempt + 1 < DOWNLOAD_ATTEMPTS:
            time.sleep(RETRY_SECONDS)
    raise RuntimeError(
        f"Could not download a verified copy of {url} ({last}). "
        f"Run {BOOTSTRAP_COMMAND} again."
    )


def extract_archive(archive: Path, destination: Path, *, expected_root: str) -> None:
    """Unpack a Node.js archive, refusing any member that escapes *destination*."""
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                _require_contained_member(member.orig_filename, expected_root)
                _require_contained_member(member.filename, expected_root)
                mode = member.external_attr >> 16
                if member.create_system == 3 and stat.S_ISLNK(mode):
                    raise RuntimeError("Node archive contains a link; not unpacked.")
            bundle.extractall(destination)
        return
    with tarfile.open(archive) as bundle:
        for member in bundle.getnames():
            _require_contained_member(member, expected_root)
        bundle.extractall(destination, filter="data")


def _require_contained_member(name: str, expected_root: str) -> None:
    parts = PurePosixPath(name).parts
    if (
        name.startswith(("/", "\\"))
        or "\\" in name
        or ":" in name
        or not parts
        or parts[0] != expected_root
        or ".." in parts
    ):
        raise RuntimeError(f"Node archive contains an unexpected path: {name!r}")


def ensure_node(cache: Path) -> NodeRuntime:
    existing = shutil.which("node")
    if existing and supported_node(existing):
        npm_cli = npm_cli_for(Path(existing), search_path=True)
        if npm_cli:
            return NodeRuntime(node=Path(existing), npm_cli=npm_cli)
    target = node_download(platform.system(), platform.machine())
    if not target:
        raise RuntimeError(
            "Install a supported Node.js version for this platform, "
            f"then run {BOOTSTRAP_COMMAND} again."
        )
    name, archive_name, _extension = target
    destination = cache / name
    runtime = node_runtime_at(destination)
    if runtime:
        return runtime
    print("Preparing the local JavaScript runtime…", flush=True)
    base = f"https://nodejs.org/dist/v{NODE_VERSION}/"
    sums = dict(
        line.split()[::-1]
        for line in fetch(base + "SHASUMS256.txt", timeout=60).decode().splitlines()
    )
    if archive_name not in sums:
        raise RuntimeError(f"Node.js {NODE_VERSION} publishes no {archive_name}.")
    temporary = Path(tempfile.mkdtemp(prefix="node-", dir=cache))
    try:
        archive = temporary / archive_name
        download_verified(base + archive_name, archive, sums[archive_name], timeout=300)
        extract_archive(archive, temporary, expected_root=name)
        if destination.exists():
            remove_tree(destination)
        (temporary / name).rename(destination)
    finally:
        remove_tree(temporary)
    runtime = node_runtime_at(destination)
    if not runtime:
        raise RuntimeError("The downloaded JavaScript runtime is missing npm.")
    return runtime


def prepare_frontend(root: Path, cache: Path) -> Path:
    frontend = root / "frontend"
    node = ensure_node(cache)
    env = node.environment()
    lock_hash = fingerprint([frontend / "package-lock.json", frontend / "package.json"])
    install_stamp = cache / "frontend-install"
    if (
        not (frontend / "node_modules").exists()
        or not install_stamp.exists()
        or install_stamp.read_text(encoding="utf-8") != lock_hash
    ):
        print("Installing the Compass interface…", flush=True)
        run(node.npm("ci", "--no-audit", "--no-fund"), cwd=frontend, env=env)
        install_stamp.write_text(lock_hash, encoding="utf-8")
    sources = [
        p
        for folder in (frontend / "src", frontend / "public")
        for p in folder.rglob("*")
        if p.is_file()
    ]
    sources += [p for p in frontend.iterdir() if p.is_file()]
    build_hash = fingerprint(sources)
    build_stamp = cache / "frontend-build"
    if (
        not (frontend / "dist/index.html").exists()
        or not build_stamp.exists()
        or build_stamp.read_text(encoding="utf-8") != build_hash
    ):
        print("Building Compass…", flush=True)
        run(node.npm("run", "build"), cwd=frontend, env=env)
        build_stamp.write_text(build_hash, encoding="utf-8")
    return frontend / "dist"


def require_free_port(port: int) -> None:
    try:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", port))
    except OSError as error:
        raise RuntimeError(
            f"Port {port} is already in use. Stop the previous Compass terminal "
            "or choose --port and --connector-port. No existing process was stopped."
        ) from error


class ManagedConnector:
    def __init__(
        self,
        uv: str,
        checkout: Path,
        profile: Path,
        port: int,
        log: Path,
        *,
        temporary: Path | None = None,
    ):
        self.command = [
            uv,
            "run",
            "--frozen",
            "--no-dev",
            "python",
            "-m",
            "linkedin_mcp_server",
        ]
        self.checkout, self.profile, self.port, self.log = checkout, profile, port, log
        self.temporary = temporary
        self.phase = "starting"
        self.process: ContainedProcess | None = None
        self.task: asyncio.Task | None = None
        self.stopping = False

    def status(self) -> dict[str, str | bool]:
        return {"managed": True, "phase": self.phase}

    def begin(self, *, login: bool = False) -> None:
        if self.task and not self.task.done():
            raise HTTPException(409, "LinkedIn setup is already running.")
        self.task = asyncio.create_task(self._run(login=login))

    async def _spawn(self, arguments: list[str]) -> None:
        if self.process is not None:
            await self.process.stop()
        command = [
            *self.command,
            "--user-data-dir",
            str(self.profile),
            "--no-auto-import",
            "--no-daemon",
            *arguments,
        ]
        env = None
        if self.temporary is not None:
            env = {
                **os.environ,
                **dict.fromkeys(("TMP", "TEMP", "TMPDIR"), str(self.temporary)),
            }
        with self.log.open("ab") as output:
            self.process = await spawn_contained(
                command,
                cwd=self.checkout,
                output=output.fileno(),
                # The launcher cache is already private and beside the log this
                # child writes to, so containment bookkeeping lives there too.
                containment_dir=self.log.parent,
                env=env,
            )

    async def _run(self, *, login: bool) -> None:
        try:
            has_session = all(
                (self.profile.parent / name).exists()
                for name in ("source-state.json", "cookies.json")
            )
            if login or not has_session:
                self.phase = "signing_in"
                print(
                    "Sign in to LinkedIn in the window that opens. "
                    "Compass never asks for your password.",
                    flush=True,
                )
                await self._spawn(["--login"])
                assert self.process is not None
                if await self.process.wait() != 0:
                    self.phase = "login_failed"
                    await self.stop_process()
                    return
            self.phase = "connecting"
            await self._spawn(
                [
                    "--transport",
                    "streamable-http",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(self.port),
                ]
            )
            assert self.process is not None
            for _ in range(120):
                if self.process.returncode is not None:
                    raise RuntimeError("Connector exited during startup")
                try:
                    _, writer = await asyncio.open_connection("127.0.0.1", self.port)
                    writer.close()
                    await writer.wait_closed()
                    break
                except OSError:
                    await asyncio.sleep(0.5)
            else:
                raise RuntimeError("Connector startup timed out")
            self.phase = "ready"
            print(
                "Compass is ready. Choose Run search when your criteria are ready.",
                flush=True,
            )
            await self.process.wait()
            if not self.stopping:
                self.phase = "failed"
                await self.stop_process()
        except asyncio.CancelledError:
            raise
        except Exception:
            self.phase = "failed"
            await self.stop_process()

    async def stop_process(self) -> None:
        process = self.process
        if process is not None:
            # Ends the connector and everything it started, including its
            # browser, and leaves every other browser on the machine alone.
            await process.stop()

    async def close(self) -> None:
        self.stopping = True
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        await self.stop_process()


class CompassFiles(StaticFiles):
    async def get_response(self, path, scope):
        # SPA fallback applies only to known browser routes, never missing API/assets.
        path = path.replace(os.sep, "/")
        if path.strip("/") in {
            "",
            ".",
            "brief",
            "search",
            "saved",
            "settings",
            "candidates",
            "how-it-works",
        } or path.startswith(("candidates/", "how-it-works/")):
            assert self.directory is not None
            return FileResponse(
                Path(self.directory) / "index.html",
                headers={"Cache-Control": "no-cache"},
            )
        return await super().get_response(path, scope)


def managed_app(
    settings: Settings, manager: ManagedConnector, dist: Path, *, login=False
):
    app = create_app(settings, retrieval_ready=lambda: manager.phase == "ready")
    original_lifespan = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with original_lifespan(app):
            manager.begin(login=login)
            try:
                yield
            finally:
                await app.state.job_queue.stop()
                await manager.close()

    app.router.lifespan_context = lifespan

    @app.get("/api/launcher")
    def launcher_status():
        return manager.status()

    @app.post("/api/launcher/login", status_code=202)
    async def login_again():
        if manager.phase not in {"login_failed", "failed"}:
            raise HTTPException(409, "LinkedIn setup is already running or connected.")
        manager.begin(login=True)
        return manager.status()

    app.mount("/", CompassFiles(directory=dist), name="compass")
    return app


async def serve(
    app,
    port: int,
    *,
    open_browser: bool,
    cache: Path | None = None,
    record: OwnerRecord | None = None,
):
    # The loop is already running here, so Uvicorn never replaces it. That
    # matters on Windows, where only the default proactor loop can start the
    # connector process at all.
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            access_log=False,
            server_header=False,
            timeout_graceful_shutdown=5,
        )
    )

    async def open_when_ready():
        while not server.started:  # noqa: ASYNC110 -- Uvicorn exposes a flag, not an event.
            await asyncio.sleep(0.1)
        url = f"http://127.0.0.1:{port}/brief"
        print(
            f"Compass: {url}\nKeep this terminal open. Ctrl+C stops Compass.",
            flush=True,
        )
        if open_browser:
            await asyncio.to_thread(webbrowser.open, url)

    async def stop_when_asked():
        # A later launch from this folder asks this instance to shut itself
        # down. Answering here means the handover runs the same clean shutdown
        # as Ctrl+C on every platform, instead of a process being killed.
        assert cache is not None and record is not None
        while not server.should_exit:
            if take_shutdown_request(cache, record):
                print(
                    "Another Compass launch asked this instance to stop. "
                    "Closing this one…",
                    flush=True,
                )
                server.should_exit = True
                return
            await asyncio.sleep(REQUEST_POLL_SECONDS)

    watchers = [asyncio.create_task(open_when_ready())]
    if cache is not None and record is not None:
        watchers.append(asyncio.create_task(stop_when_asked()))
    try:
        await server.serve()
    finally:
        for watcher in watchers:
            watcher.cancel()
        for watcher in watchers:
            with contextlib.suppress(asyncio.CancelledError):
                await watcher


def main():
    # The bootstrap uses a private dashboard environment. Child uv commands must
    # use their own connector environment, never replace this running interpreter.
    os.environ.pop("UV_PROJECT_ENVIRONMENT", None)
    parser = argparse.ArgumentParser(
        description="Set up and open Compass with one command."
    )
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--connector-port", type=int, default=8000)
    parser.add_argument(
        "--login", action="store_true", help="Sign in again before starting retrieval"
    )
    parser.add_argument(
        "--no-open", action="store_true", help="Print the URL without opening Compass"
    )
    parser.add_argument(
        "--setup-only",
        action="store_true",
        help="Install and build without starting services or LinkedIn login",
    )
    args = parser.parse_args()
    cache = PROJECT_ROOT / ".compass"
    if not IS_WINDOWS:
        os.umask(0o077)
    uv = os.environ.get("COMPASS_UV") or shutil.which("uv")
    if not uv:
        parser.error(f"Start Compass with {BOOTSTRAP_COMMAND}")
    try:
        create_private_directories(cache)
        if not args.setup_only and (
            args.port == args.connector_port
            or not all(1 <= p <= 65535 for p in (args.port, args.connector_port))
        ):
            raise RuntimeError("Choose two different ports between 1 and 65535.")
        with launcher_lock(cache, PROJECT_ROOT, setup_only=args.setup_only) as lock:
            if not args.setup_only:
                require_free_port(args.port)
                require_free_port(args.connector_port)
            checkout = ensure_connector(PROJECT_ROOT, cache, uv)
            dist = prepare_frontend(PROJECT_ROOT, cache)
            if args.setup_only:
                print(f"Compass is installed. Run {BOOTSTRAP_COMMAND} to open it.")
                return
            profile = Path.home() / ".compass-linkedin" / "profile"
            create_private_directories(profile.parent)
            temporary = None
            if IS_WINDOWS:
                # The connector refuses to install its browser under a %TEMP%
                # that another local account may modify (sandbox tools such as
                # CodexSandboxUsers add such grants), so it gets a private one.
                temporary = profile.parent / "tmp"
                create_private_directories(temporary)
            manager = ManagedConnector(
                uv,
                checkout,
                profile,
                args.connector_port,
                cache / "connector.log",
                temporary=temporary,
            )
            settings = Settings(
                host="127.0.0.1",
                port=args.port,
                frontend_host="127.0.0.1",
                frontend_port=args.port,
                mcp_url=f"http://127.0.0.1:{args.connector_port}/mcp",
            )
            record = own_record(RUNNING, PROJECT_ROOT)
            # A request left by a launch this instance never answered must not
            # close the app the moment it opens.
            clear_shutdown_request(cache)
            lock.write_state(record.serialize())
            asyncio.run(
                serve(
                    managed_app(settings, manager, dist, login=args.login),
                    args.port,
                    open_browser=not args.no_open,
                    cache=cache,
                    record=record,
                )
            )
    except KeyboardInterrupt:
        pass
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(
            1,
            f"Compass could not start: {error}\n"
            f"Run {BOOTSTRAP_COMMAND} again after resolving this. "
            f"Logs: {cache / 'connector.log'}\n",
        )


if __name__ == "__main__":
    main()
