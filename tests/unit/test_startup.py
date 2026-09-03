from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import pytest


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


@pytest.mark.parametrize("host", ["::1%lo0", "::ffff:127.0.0.1"])
def test_entrypoint_rejects_scoped_and_mapped_ipv6_before_database(
    tmp_path, host: str
) -> None:
    database_path = tmp_path / "unsafe-ip.db"
    result = subprocess.run(
        [sys.executable, "-m", "linkedin_dashboard"],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
        env={
            **os.environ,
            "HOST": host,
            "DB_PATH": str(database_path),
        },
    )

    assert result.returncode != 0
    assert "loopback" in f"{result.stdout}\n{result.stderr}"
    assert not database_path.exists()


def test_programmatic_wildcard_listener_is_rejected_before_database(
    tmp_path,
) -> None:
    os.chmod(tmp_path, 0o700)
    port = _free_ipv4_port()
    database_path = tmp_path / "wildcard.db"
    source = (
        "import uvicorn\n"
        "from linkedin_dashboard.main import create_app\n"
        "from linkedin_dashboard.settings import Settings\n"
        "settings = Settings()\n"
        "uvicorn.run(create_app(settings), host='0.0.0.0', "
        "port=settings.port, access_log=False)\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", source],
        cwd=Path(__file__).resolve().parents[2],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={
            **os.environ,
            "HOST": "127.0.0.1",
            "PORT": str(port),
            "DB_PATH": str(database_path),
        },
    )

    try:
        deadline = time.monotonic() + 10
        response_body = ""
        while time.monotonic() < deadline:
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                raise AssertionError(
                    f"wildcard server exited early ({process.returncode}):\n"
                    f"{stdout}\n{stderr}"
                )
            try:
                urlopen(f"http://127.0.0.1:{port}/api/health", timeout=0.25)
            except HTTPError as error:
                assert error.status == 503
                response_body = error.read().decode()
                break
            except (URLError, TimeoutError):
                time.sleep(0.05)
            else:
                raise AssertionError("wildcard listener served the dashboard")
        else:
            raise AssertionError("wildcard listener did not become reachable")
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    assert "does not match configured loopback binding" in response_body
    assert not database_path.exists()
