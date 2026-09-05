"""Launcher checks use fake subprocesses and local HTTP; never LinkedIn."""

import asyncio
import socket
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
