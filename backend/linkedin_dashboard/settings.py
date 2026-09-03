from __future__ import annotations

import ipaddress
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_loopback_host(value: str) -> str:
    """Return the runtime-safe canonical form of a loopback host."""
    host = value.strip()
    if host.casefold() == "localhost":
        return "localhost"

    if host.startswith("[") or host.endswith("]"):
        if not (host.startswith("[") and host.endswith("]")):
            raise ValueError(f"host must be loopback-only; got {value!r}")
        host = host[1:-1]
        try:
            address = ipaddress.ip_address(host)
        except ValueError as error:
            raise ValueError(f"host must be loopback-only; got {value!r}") from error
        if address.version != 6:
            raise ValueError(
                "host must be loopback-only; brackets are valid only around IPv6"
            )
    else:
        try:
            address = ipaddress.ip_address(host)
        except ValueError as error:
            raise ValueError(f"host must be loopback-only; got {value!r}") from error

    if not address.is_loopback:
        raise ValueError(f"host must be loopback-only; got {value!r}")
    return address.compressed


def is_loopback_host(value: str) -> bool:
    """Return whether *value* can be normalized to a loopback bind host."""
    try:
        normalize_loopback_host(value)
    except ValueError:
        return False
    return True


def format_url_host(host: str) -> str:
    """Format a normalized host for use in an HTTP authority."""
    return f"[{host}]" if ":" in host else host


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
    frontend_port: int = Field(default=5173, ge=1, le=65535)
    mcp_url: str = "http://127.0.0.1:8000/mcp"
    db_path: Path = Path("~/.linkedin-dashboard/session.db")
    llm_provider: str = "null"
    send_enabled: bool = False

    @field_validator("host", "frontend_host")
    @classmethod
    def require_loopback_host(cls, value: str) -> str:
        return normalize_loopback_host(value)

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

    @property
    def frontend_origin(self) -> str:
        host = format_url_host(self.frontend_host)
        return f"http://{host}:{self.frontend_port}"
