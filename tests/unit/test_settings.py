from __future__ import annotations

import socket
from pathlib import Path
from typing import Any, cast

import pytest
from linkedin_dashboard import main as main_module
from linkedin_dashboard.security import _resolved_addresses
from linkedin_dashboard.settings import Settings, is_loopback_host
from pydantic import ValidationError


@pytest.mark.parametrize(
    ("host", "normalized"),
    [
        ("127.0.0.1", "127.0.0.1"),
        ("127.0.0.2", "127.0.0.2"),
        ("::1", "::1"),
        ("[::1]", "::1"),
        ("0:0:0:0:0::1", "::1"),
        ("0:0::0:1", "::1"),
        ("::0:1", "::1"),
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
        "localhost",
        "LOCALHOST",
        "",
        "[127.0.0.1]",
        "::1%lo0",
        "[::1%25lo0]",
        "::ffff:127.0.0.1",
        "[::ffff:127.0.0.1]",
        "[::1",
        "::1]",
    ],
)
def test_non_loopback_backend_host_is_rejected(host: str, tmp_path) -> None:
    with pytest.raises(ValidationError, match="loopback"):
        Settings(host=host, db_path=tmp_path / "dashboard.db")


def test_non_loopback_mcp_url_is_rejected(tmp_path) -> None:
    with pytest.raises(ValidationError, match="loopback host"):
        Settings(
            mcp_url="https://mcp.example.com/mcp",
            db_path=tmp_path / "dashboard.db",
        )


def test_localhost_mcp_url_is_rejected(tmp_path) -> None:
    with pytest.raises(ValidationError, match="numeric loopback"):
        Settings(
            mcp_url="http://localhost:8000/mcp",
            db_path=tmp_path / "dashboard.db",
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://[::1%25lo0]:8000/mcp",
        "http://[::ffff:127.0.0.1]:8000/mcp",
    ],
)
def test_scoped_and_mapped_mcp_ipv6_are_rejected(url: str, tmp_path) -> None:
    with pytest.raises(ValidationError, match=r"unscoped|numeric loopback"):
        Settings(mcp_url=url, db_path=tmp_path / "dashboard.db")


def test_literal_loopbacks_never_use_dns(monkeypatch, tmp_path) -> None:
    def hostile_resolution(*args, **kwargs):
        del args, kwargs
        raise AssertionError("DNS resolution must not be consulted")

    monkeypatch.setattr(socket, "getaddrinfo", hostile_resolution)

    settings = Settings(
        host="0:0::0:1",
        frontend_host="127.0.0.2",
        mcp_url="http://[::0:1]:8000/mcp",
        db_path=tmp_path / "dashboard.db",
    )

    assert settings.host == "::1"
    assert settings.frontend_host == "127.0.0.2"
    assert settings.mcp_url == "http://[::1]:8000/mcp"
    assert _resolved_addresses(settings.host, settings.port) == {"::1"}


@pytest.mark.parametrize(
    ("host", "origin"),
    [
        ("127.0.0.1", "http://127.0.0.1"),
        ("::1", "http://[::1]"),
    ],
)
def test_frontend_origin_omits_default_http_port(
    tmp_path, host: str, origin: str
) -> None:
    settings = Settings(
        frontend_host=host,
        frontend_port=80,
        db_path=tmp_path / "port-80.db",
    )

    assert settings.frontend_origin == origin


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:not-a-port/mcp",
        "http://127.0.0.1:0/mcp",
        "http://127.0.0.1:65536/mcp",
    ],
)
def test_mcp_url_invalid_port_is_rejected(url: str, tmp_path) -> None:
    with pytest.raises(ValidationError, match="valid port"):
        Settings(mcp_url=url, db_path=tmp_path / "dashboard.db")


@pytest.mark.parametrize("provider", ["openai", "local", "NULL", ""])
def test_llm_provider_is_locked_to_null_through_m5(provider: str, tmp_path) -> None:
    with pytest.raises(ValidationError, match="null"):
        Settings(
            llm_provider=cast(Any, provider),
            db_path=tmp_path / "dashboard.db",
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://operator@127.0.0.1:8000/mcp",
        "http://operator:secret@127.0.0.1:8000/mcp",
    ],
)
def test_mcp_url_userinfo_is_rejected(url: str, tmp_path) -> None:
    with pytest.raises(ValidationError, match="userinfo or credentials"):
        Settings(mcp_url=url, db_path=tmp_path / "dashboard.db")


def test_database_path_inside_repository_is_rejected() -> None:
    repository_path = Path(__file__).resolve().parents[2] / "unsafe.db"

    with pytest.raises(ValidationError, match="outside the project repository"):
        Settings(db_path=repository_path)


def test_database_path_final_symlink_is_rejected(tmp_path) -> None:
    target = tmp_path / "target.db"
    target.touch()
    link = tmp_path / "linked.db"
    link.symlink_to(target)

    with pytest.raises(ValidationError, match="symbolic link"):
        Settings(db_path=link)


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
