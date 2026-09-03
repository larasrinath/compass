from __future__ import annotations

import pytest
from linkedin_dashboard import main as main_module
from linkedin_dashboard.settings import Settings, is_loopback_host
from pydantic import ValidationError


@pytest.mark.parametrize(
    ("host", "normalized"),
    [
        ("127.0.0.1", "127.0.0.1"),
        ("127.0.0.2", "127.0.0.2"),
        ("::1", "::1"),
        ("[::1]", "::1"),
        ("LOCALHOST", "localhost"),
    ],
)
def test_loopback_hosts_are_normalized_for_runtime(
    host: str, normalized: str, tmp_path
) -> None:
    settings = Settings(
        host=host,
        frontend_host=host,
        mcp_url="http://127.0.0.1:8000/mcp",
        db_path=tmp_path / "dashboard.db",
    )

    assert settings.host == normalized
    assert settings.frontend_host == normalized
    assert is_loopback_host(host)


@pytest.mark.parametrize(
    "host",
    [
        "0.0.0.0",
        "192.168.1.20",
        "example.com",
        "",
        "[127.0.0.1]",
        "[::1",
        "::1]",
    ],
)
def test_non_loopback_backend_host_is_rejected(host: str, tmp_path) -> None:
    with pytest.raises(ValidationError, match="loopback-only"):
        Settings(host=host, db_path=tmp_path / "dashboard.db")


def test_non_loopback_mcp_url_is_rejected(tmp_path) -> None:
    with pytest.raises(ValidationError, match="loopback host"):
        Settings(
            mcp_url="https://mcp.example.com/mcp",
            db_path=tmp_path / "dashboard.db",
        )


def test_ipv6_frontend_origin_uses_bracketed_authority(tmp_path) -> None:
    settings = Settings(
        frontend_host="[::1]",
        frontend_port=5174,
        db_path=tmp_path / "dashboard.db",
    )

    assert settings.frontend_origin == "http://[::1]:5174"


def test_run_passes_normalized_ipv6_host_to_uvicorn(monkeypatch, tmp_path) -> None:
    called: dict[str, object] = {}

    def fake_run(app, **kwargs) -> None:
        called.update(app=app, **kwargs)

    monkeypatch.setenv("HOST", "[::1]")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "runtime.db"))
    monkeypatch.setattr(main_module.uvicorn, "run", fake_run)

    main_module.run()

    assert called["host"] == "::1"
