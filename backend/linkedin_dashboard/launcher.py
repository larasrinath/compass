"""One-command local setup and owned-process lifecycle. Never performs a search."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import fcntl
import hashlib
import os
import platform
import shutil
import signal
import socket
import subprocess
import tarfile
import tempfile
import urllib.request
import webbrowser
from pathlib import Path

import psutil
import uvicorn
from fastapi import HTTPException
from fastapi.responses import FileResponse
from starlette.staticfiles import StaticFiles

from linkedin_dashboard.main import create_app
from linkedin_dashboard.settings import PROJECT_ROOT, Settings

UPSTREAM = "https://github.com/stickerdaniel/linkedin-mcp-server.git"
CONNECTOR_REVISION = "f410bfdc32569f8763fde11338b24ec6a0797f0d"
NODE_VERSION = "22.22.0"
PATCHES = ("people-pagination.patch", "parallel-profiles.patch")


def run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
    subprocess.run(command, cwd=cwd, env=env, check=True)


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
        with tempfile.TemporaryDirectory(prefix="connector-", dir=cache) as temporary:
            checkout = Path(temporary) / "source"
            run(
                ["git", "clone", "--quiet", "--no-checkout", UPSTREAM, str(checkout)],
                cwd=root,
            )
            run(
                ["git", "checkout", "--quiet", "--detach", CONNECTOR_REVISION],
                cwd=checkout,
            )
            for patch in patches:
                run(["git", "apply", "--check", str(patch)], cwd=checkout)
                run(["git", "apply", str(patch)], cwd=checkout)
            # Move before installing: virtual-environment scripts embed absolute paths.
            if destination.exists():
                shutil.rmtree(destination)
            checkout.rename(destination)
        run([uv, "sync", "--frozen", "--no-dev", "--python", "3.13"], cwd=destination)
        marker.write_text(version)
    return destination


def supported_node(executable: str) -> bool:
    try:
        version = subprocess.check_output([executable, "--version"], text=True).strip()
        major, minor, *_ = map(int, version.lstrip("v").split("."))
        return (
            (major == 20 and minor >= 19)
            or (major == 22 and minor >= 12)
            or major >= 24
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def ensure_node(cache: Path) -> Path:
    existing = shutil.which("node")
    if existing and shutil.which("npm") and supported_node(existing):
        return Path(existing).parent
    system = {"Darwin": "darwin", "Linux": "linux"}.get(platform.system())
    arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64", "AMD64": "x64"}.get(
        platform.machine()
    )
    if not system or not arch:
        raise RuntimeError(
            "Install a supported Node.js version for this platform, "
            "then run ./compass again."
        )
    name = f"node-v{NODE_VERSION}-{system}-{arch}"
    destination = cache / name
    if not (destination / "bin/node").exists():
        print("Preparing the local JavaScript runtime…", flush=True)
        base = f"https://nodejs.org/dist/v{NODE_VERSION}/"
        with urllib.request.urlopen(base + "SHASUMS256.txt", timeout=60) as response:
            sums = dict(
                line.split()[::-1] for line in response.read().decode().splitlines()
            )
        archive_name = name + ".tar.gz"
        with tempfile.TemporaryDirectory(prefix="node-", dir=cache) as temporary:
            archive = Path(temporary) / archive_name
            with urllib.request.urlopen(base + archive_name, timeout=120) as response:
                with archive.open("wb") as output:
                    shutil.copyfileobj(response, output)
            if hashlib.sha256(archive.read_bytes()).hexdigest() != sums[archive_name]:
                raise RuntimeError(
                    "Node download checksum did not match. Run ./compass again."
                )
            with tarfile.open(archive) as bundle:
                bundle.extractall(temporary, filter="data")
            if destination.exists():
                shutil.rmtree(destination)
            (Path(temporary) / name).rename(destination)
    return destination / "bin"


def prepare_frontend(root: Path, cache: Path) -> Path:
    frontend = root / "frontend"
    node_bin = ensure_node(cache)
    env = {
        **os.environ,
        "PATH": str(node_bin) + os.pathsep + os.environ.get("PATH", ""),
    }
    npm = shutil.which("npm", path=env["PATH"])
    if not npm:
        raise RuntimeError("The JavaScript runtime is missing npm.")
    lock_hash = fingerprint([frontend / "package-lock.json", frontend / "package.json"])
    install_stamp = cache / "frontend-install"
    if (
        not (frontend / "node_modules").exists()
        or not install_stamp.exists()
        or install_stamp.read_text() != lock_hash
    ):
        print("Installing the Compass interface…", flush=True)
        run([npm, "ci", "--no-audit", "--no-fund"], cwd=frontend, env=env)
        install_stamp.write_text(lock_hash)
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
        or build_stamp.read_text() != build_hash
    ):
        print("Building Compass…", flush=True)
        run([npm, "run", "build"], cwd=frontend, env=env)
        build_stamp.write_text(build_hash)
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


def find_launcher_owner(lock_path: Path, root: Path) -> psutil.Process | None:
    """Identify a launcher by its module, repository and open lock file."""
    owners = []
    for process in psutil.process_iter(["pid", "cmdline", "cwd"]):
        try:
            if process.pid == os.getpid():
                continue
            arguments = process.info["cmdline"] or []
            if not any(
                arguments[index : index + 2] == ["-m", "linkedin_dashboard.launcher"]
                for index in range(len(arguments) - 1)
            ):
                continue
            if (
                not process.info["cwd"]
                or Path(process.info["cwd"]).resolve() != root.resolve()
            ):
                continue
            if any(
                Path(item.path).resolve() == lock_path.resolve()
                for item in process.open_files()
            ):
                owners.append(process)
        except (psutil.Error, OSError):
            continue
    return owners[0] if len(owners) == 1 else None


@contextlib.contextmanager
def launcher_lock(cache: Path, root: Path, *, setup_only: bool = False):
    # Serialize takeover so simultaneous launches cannot stop the same instance.
    with (cache / "launcher-restart.lock").open("a+") as takeover:
        try:
            fcntl.flock(takeover, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(
                "Another Compass launch is restarting. Wait for it to finish."
            ) from None
        lock_path = cache / "launcher.lock"
        lock = lock_path.open("a+")
        try:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                if setup_only:
                    raise RuntimeError(
                        "Compass is running. Stop it before setup-only maintenance."
                    ) from None
                lock.seek(0)
                if lock.read().strip() == "preparing":
                    raise RuntimeError(
                        "Compass is still preparing its installation. "
                        "Wait for the existing launch to finish."
                    ) from None
                owner = find_launcher_owner(lock_path, root)
                if owner is None:
                    raise RuntimeError(
                        "Cannot verify the previous Compass process. "
                        "No process was stopped."
                    ) from None
                print("Restarting the previous Compass instance…", flush=True)
                try:
                    owner.terminate()
                    owner.wait(timeout=25)
                except psutil.NoSuchProcess:
                    pass
                except psutil.TimeoutExpired:
                    raise RuntimeError(
                        "The previous Compass instance is still shutting down. "
                        "Try again shortly."
                    ) from None
                except psutil.Error as error:
                    raise RuntimeError(
                        "Could not stop the previous Compass process."
                    ) from error
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            lock.seek(0)
            lock.truncate()
            lock.write("preparing")
            lock.flush()
        except BaseException:
            lock.close()
            raise
    try:
        yield lock
    finally:
        lock.close()


class ManagedConnector:
    def __init__(self, uv: str, checkout: Path, profile: Path, port: int, log: Path):
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
        self.phase = "starting"
        self.process: asyncio.subprocess.Process | None = None
        self.task: asyncio.Task | None = None
        self.stopping = False

    def status(self) -> dict[str, str | bool]:
        return {"managed": True, "phase": self.phase}

    def begin(self, *, login: bool = False) -> None:
        if self.task and not self.task.done():
            raise HTTPException(409, "LinkedIn setup is already running.")
        self.task = asyncio.create_task(self._run(login=login))

    async def _spawn(self, arguments: list[str]) -> None:
        with self.log.open("ab") as output:
            self.process = await asyncio.create_subprocess_exec(
                *self.command,
                "--user-data-dir",
                str(self.profile),
                "--no-auto-import",
                *arguments,
                cwd=self.checkout,
                stdout=output,
                stderr=output,
                start_new_session=True,
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
        except asyncio.CancelledError:
            raise
        except Exception:
            self.phase = "failed"
            await self.stop_process()

    async def stop_process(self) -> None:
        process = self.process
        if process and process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(process.wait(), timeout=8)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                await process.wait()

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


async def serve(app, port: int, *, open_browser: bool):
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

    opener = asyncio.create_task(open_when_ready())
    try:
        await server.serve()
    finally:
        opener.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await opener


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
    cache.mkdir(mode=0o700, exist_ok=True)
    os.umask(0o077)
    uv = os.environ.get("COMPASS_UV") or shutil.which("uv")
    if not uv:
        parser.error("Start Compass with ./compass")
    try:
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
                print("Compass is installed. Run ./compass to open it.")
                return
            profile = Path.home() / ".compass-linkedin" / "profile"
            manager = ManagedConnector(
                uv, checkout, profile, args.connector_port, cache / "connector.log"
            )
            settings = Settings(
                host="127.0.0.1",
                port=args.port,
                frontend_host="127.0.0.1",
                frontend_port=args.port,
                mcp_url=f"http://127.0.0.1:{args.connector_port}/mcp",
            )
            lock.seek(0)
            lock.truncate()
            lock.write("running")
            lock.flush()
            asyncio.run(
                serve(
                    managed_app(settings, manager, dist, login=args.login),
                    args.port,
                    open_browser=not args.no_open,
                )
            )
    except KeyboardInterrupt:
        pass
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(
            1,
            f"Compass could not start: {error}\n"
            "Run ./compass again after resolving this. "
            f"Logs: {cache / 'connector.log'}\n",
        )


if __name__ == "__main__":
    main()
