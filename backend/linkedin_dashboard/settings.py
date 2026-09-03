from __future__ import annotations

import ipaddress
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def is_loopback_host(value: str) -> bool:
    """Return whether *value* identifies only this machine."""
    host = value.strip().removeprefix("[").removesuffix("]")
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class Settings(BaseSettings):
    """Process-local configuration that is never serialized to the frontend."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = Field(default=8787, ge=1, le=65535)
    frontend_host: str = "127.0.0.1"
    mcp_url: str = "http://127.0.0.1:8000/mcp"
    db_path: Path = Path("~/.linkedin-dashboard/session.db")
    llm_provider: str = "null"
    send_enabled: bool = False

    @field_validator("host", "frontend_host")
    @classmethod
    def require_loopback_host(cls, value: str) -> str:
        if not is_loopback_host(value):
            raise ValueError(f"host must be loopback-only; got {value!r}")
        return value

    @field_validator("mcp_url")
    @classmethod
    def require_loopback_mcp_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("MCP_URL must be an absolute HTTP URL")
        if not is_loopback_host(parsed.hostname):
            raise ValueError("MCP_URL must target a loopback host")
        return value

    @field_validator("db_path")
    @classmethod
    def normalize_database_path(cls, value: Path) -> Path:
        return value.expanduser().resolve()
