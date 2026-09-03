from __future__ import annotations

import asyncio
import ipaddress
from collections.abc import Callable
from typing import Any, Protocol, Self
from urllib.parse import urlparse

import httpx
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from pydantic import BaseModel, ConfigDict

from linkedin_dashboard.mcp.envelope import (
    MalformedMCPResponse,
    MCPResponseEnvelope,
    parse_response_envelope,
    serialize_json_object,
)
from linkedin_dashboard.mcp.errors import MCPClientError, error_details

DEFAULT_MCP_TIMEOUT_SECONDS = 240.0
MAX_MCP_RESPONSE_BYTES = 16 * 1024 * 1024
_FORBIDDEN_FORWARDING_HEADERS = {
    "authorization",
    "cookie",
    "forwarded",
    "host",
    "origin",
    "proxy-authorization",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
}


class ToolDescription(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str


class ClientSession(Protocol):
    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self, exc_type: object, exc_val: object, exc_tb: object, /
    ) -> None: ...

    async def list_tools(self) -> list[Any]: ...

    async def call_tool_mcp(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        timeout: float | None = None,  # noqa: ASYNC109 - mirrors FastMCP API
    ) -> object: ...


ClientFactory = Callable[[str, float], ClientSession]


class MCPClient:
    """A short-lived direct client for one explicit MCP operation at a time."""

    def __init__(
        self,
        url: str,
        *,
        timeout_seconds: float = DEFAULT_MCP_TIMEOUT_SECONDS,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self.url = _require_direct_loopback_endpoint(url)
        if timeout_seconds <= 0:
            raise ValueError("MCP timeout must be positive")
        self.timeout_seconds = timeout_seconds
        self._client_factory = client_factory or _default_client_factory

    async def list_tools(self) -> tuple[ToolDescription, ...]:
        try:
            async with self._client_factory(self.url, self.timeout_seconds) as client:
                tools = await client.list_tools()
            return tuple(
                ToolDescription.model_validate(serialize_json_object(tool))
                for tool in tools
            )
        except asyncio.CancelledError:
            raise
        except MCPClientError:
            raise
        except Exception as error:
            raise MCPClientError(error_details(error)) from error

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> MCPResponseEnvelope:
        try:
            async with self._client_factory(self.url, self.timeout_seconds) as client:
                raw_result = await client.call_tool_mcp(
                    name,
                    arguments,
                    timeout=self.timeout_seconds,
                )
            response = parse_response_envelope(raw_result)
            if len(response.as_json().encode("utf-8")) > MAX_MCP_RESPONSE_BYTES:
                raise MCPClientError(
                    error_details(ValueError("MCP response exceeded the size limit"))
                )
            return response
        except asyncio.CancelledError:
            raise
        except MalformedMCPResponse as error:
            raise MCPClientError(
                error_details(error, partial_payload=error.partial_payload)
            ) from error
        except MCPClientError:
            raise
        except Exception as error:
            raise MCPClientError(error_details(error)) from error


def _default_client_factory(url: str, timeout_seconds: float) -> ClientSession:
    # Intentionally no headers and no auth: this is the approved direct-server
    # topology. A fresh transport prevents stale MCP session reuse.
    transport = StreamableHttpTransport(
        url=url,
        httpx_client_factory=_direct_httpx_client_factory,
    )
    return Client(transport=transport, timeout=timeout_seconds)


def _direct_httpx_client_factory(
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None,
) -> httpx.AsyncClient:
    """Build a contained loopback client, overriding FastMCP's redirect default."""
    headers = dict(headers or {})
    forbidden = _FORBIDDEN_FORWARDING_HEADERS.intersection(
        str(name).casefold() for name in headers
    )
    forbidden.update(
        str(name).casefold()
        for name in headers
        if str(name).casefold().startswith("x-forwarded-")
    )
    if forbidden:
        raise ValueError("MCP transport attempted to add a forbidden header")
    if auth is not None:
        raise ValueError("MCP transport authentication is disabled")
    return httpx.AsyncClient(
        headers=headers,
        auth=None,
        follow_redirects=False,
        timeout=timeout,
        trust_env=False,
    )


def _require_direct_loopback_endpoint(value: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname is None
        or parsed.path != "/mcp"
        or parsed.params
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("MCP endpoint must be direct HTTP /mcp on numeric loopback")
    try:
        address = ipaddress.ip_address(parsed.hostname)
        port = parsed.port
    except ValueError as error:
        raise ValueError(
            "MCP endpoint must be direct HTTP /mcp on numeric loopback"
        ) from error
    if not address.is_loopback or (
        address.version == 6
        and (address.ipv4_mapped is not None or address.compressed != "::1")
    ):
        raise ValueError("MCP endpoint must use a numeric loopback address")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("MCP endpoint port is invalid")
    host = f"[{address.compressed}]" if address.version == 6 else address.compressed
    authority = f"{host}:{port}" if port is not None else host
    return parsed._replace(netloc=authority).geturl()
