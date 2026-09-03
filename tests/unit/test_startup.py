from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


def _free_ipv4_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _run_unsafe_uvicorn_target(
    target: str, extra_arguments: Sequence[str], tmp_path: Path
) -> subprocess.CompletedProcess[str]:
    port = _free_ipv4_port()
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "uvicorn",
            target,
            *extra_arguments,
            "--host",
            "0.0.0.0",
            "--port",
            str(port),
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
        env={**os.environ, "DB_PATH": str(tmp_path / "must-not-exist.db")},
    )


def test_direct_uvicorn_wildcard_bind_has_no_application_surface(tmp_path) -> None:
    result = _run_unsafe_uvicorn_target("linkedin_dashboard.main:app", (), tmp_path)

    assert result.returncode != 0
    assert 'Attribute "app" not found' in f"{result.stdout}\n{result.stderr}"
    assert not (tmp_path / "must-not-exist.db").exists()


def test_direct_uvicorn_wildcard_bind_cannot_call_application_factory(
    tmp_path,
) -> None:
    result = _run_unsafe_uvicorn_target(
        "linkedin_dashboard.main:create_app", ("--factory",), tmp_path
    )

    assert result.returncode != 0
    output = f"{result.stdout}\n{result.stderr}"
    assert "missing 1 required positional argument" in output
    assert not (tmp_path / "must-not-exist.db").exists()


def test_documented_module_entrypoint_starts_on_loopback(tmp_path) -> None:
    os.chmod(tmp_path, 0o700)
    port = _free_ipv4_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "linkedin_dashboard"],
        cwd=Path(__file__).resolve().parents[2],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={
            **os.environ,
            "HOST": "127.0.0.1",
            "PORT": str(port),
            "DB_PATH": str(tmp_path / "entrypoint.db"),
        },
    )

    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                raise AssertionError(
                    f"entrypoint exited early ({process.returncode}):\n"
                    f"{stdout}\n{stderr}"
                )
            try:
                with urlopen(
                    f"http://127.0.0.1:{port}/api/health", timeout=0.25
                ) as response:
                    assert response.status == 200
                    break
            except (URLError, TimeoutError):
                time.sleep(0.05)
        else:
            raise AssertionError("entrypoint did not become healthy")
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    assert (tmp_path / "entrypoint.db").exists()
