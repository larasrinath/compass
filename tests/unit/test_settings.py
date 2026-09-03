from __future__ import annotations

import pytest
from linkedin_dashboard.settings import Settings, is_loopback_host
from pydantic import ValidationError


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "[::1]", "localhost"])
def test_loopback_hosts_are_accepted(host: str, tmp_path) -> None:
    settings = Settings(
        host=host,
        frontend_host=host,
        mcp_url="http://127.0.0.1:8000/mcp",
        db_path=tmp_path / "dashboard.db",
    )

    assert settings.host == host
    assert is_loopback_host(host)


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20", "example.com", ""])
def test_non_loopback_backend_host_is_rejected(host: str, tmp_path) -> None:
    with pytest.raises(ValidationError, match="loopback-only"):
        Settings(host=host, db_path=tmp_path / "dashboard.db")


def test_non_loopback_mcp_url_is_rejected(tmp_path) -> None:
    with pytest.raises(ValidationError, match="loopback host"):
        Settings(
            mcp_url="https://mcp.example.com/mcp",
            db_path=tmp_path / "dashboard.db",
        )
