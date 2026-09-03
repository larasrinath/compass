from __future__ import annotations

import asyncio
import ipaddress
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
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
RawResponseCapture = Callable[[dict[str, Any]], Awaitable[None]]
ProgressCapture = Callable[[float, float | None, str | None], Awaitable[None]]


class MCPClient:
    """A short-lived direct client for one explicit MCP operation at a time."""

    __slots__ = ("_client_factory", "_endpoint", "_timeout_seconds")

    def __setattr__(self, name: str, value: object) -> None:
        if name in {"_endpoint", "_timeout_seconds"} and hasattr(self, name):
            raise AttributeError(f"{name.removeprefix('_')} is immutable")
        object.__setattr__(self, name, value)

    def __init__(
        self,
        url: str,
        *,
        timeout_seconds: float = DEFAULT_MCP_TIMEOUT_SECONDS,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self._endpoint = _require_direct_loopback_endpoint(url)
        if timeout_seconds <= 0:
            raise ValueError("MCP timeout must be positive")
        self._timeout_seconds = timeout_seconds
        self._client_factory = client_factory or _default_client_factory

    @property
    def url(self) -> str:
        return self._endpoint

    @property
    def timeout_seconds(self) -> float:
        return self._timeout_seconds

    def _operation_endpoint(self) -> str:
        # Revalidate even private state so post-construction mutation cannot
        # redirect a later explicit operation.
        return _require_direct_loopback_endpoint(self._endpoint)

    async def list_tools(self) -> tuple[ToolDescription, ...]:
        try:
            endpoint = self._operation_endpoint()
            async with self._client_factory(endpoint, self.timeout_seconds) as client:
                tools = await client.list_tools()
            raw_tools = [serialize_json_object(tool) for tool in tools]
            if _json_size(raw_tools) > MAX_MCP_RESPONSE_BYTES:
                raise MCPClientError(
                    error_details(ValueError("MCP response exceeded the size limit"))
                )
            return tuple(ToolDescription.model_validate(tool) for tool in raw_tools)
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
        *,
        raw_response_capture: RawResponseCapture | None = None,
        progress_capture: ProgressCapture | None = None,
    ) -> MCPResponseEnvelope:
        try:
            endpoint = self._operation_endpoint()
            async with self._client_factory(endpoint, self.timeout_seconds) as client:
                call_options: dict[str, Any] = {"timeout": self.timeout_seconds}
                if progress_capture is not None:
                    call_options["progress_handler"] = progress_capture
                raw_result = await client.call_tool_mcp(name, arguments, **call_options)
            raw_snapshot = serialize_json_object(raw_result)
            if _json_size(raw_snapshot) > MAX_MCP_RESPONSE_BYTES:
                raise MCPClientError(
                    error_details(ValueError("MCP response exceeded the size limit"))
                )
            if raw_response_capture is not None:
                await raw_response_capture(raw_snapshot)
            response = parse_response_envelope(raw_result)
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


def _json_size(value: object) -> int:
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )


def _default_client_factory(url: str, timeout_seconds: float) -> ClientSession:
    # Intentionally no headers and no auth: this is the approved direct-server
    # topology. A fresh transport prevents stale MCP session reuse.
    transport = StreamableHttpTransport(
        url=url,
        httpx_client_factory=_DirectHttpxClientFactory(url),
    )
    return Client(transport=transport, timeout=timeout_seconds)


@dataclass(frozen=True, slots=True)
class _DirectHttpxClientFactory:
    endpoint: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "endpoint", _require_direct_loopback_endpoint(self.endpoint)
        )

    def __call__(
        self,
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
        *,
        follow_redirects: bool = False,
    ) -> httpx.AsyncClient:
        # FastMCP 3.4.4 explicitly requests follow_redirects=True even for a
        # custom factory. The direct-server boundary intentionally ignores it.
        del follow_redirects
        return _direct_httpx_client(
            self.endpoint,
            headers=headers,
            timeout=timeout,
            auth=auth,
        )


def _direct_httpx_client(
    endpoint: str,
    *,
    headers: dict[str, str] | None,
    timeout: httpx.Timeout | None,
    auth: httpx.Auth | None,
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
        event_hooks={"request": [_RequestBoundary(endpoint)]},
    )


@dataclass(frozen=True, slots=True)
class _RequestBoundary:
    endpoint: str

    async def __call__(self, request: httpx.Request) -> None:
        if _require_direct_loopback_endpoint(str(request.url)) != self.endpoint:
            raise httpx.RequestError(
                "MCP request left its configured loopback endpoint",
                request=request,
            )
        header_names = {name.casefold() for name in request.headers}
        forbidden = (_FORBIDDEN_FORWARDING_HEADERS - {"host"}).intersection(
            header_names
        )
        forbidden.update(
            name for name in header_names if name.startswith("x-forwarded-")
        )
        if forbidden:
            raise httpx.RequestError(
                "MCP request contained a forbidden header",
                request=request,
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
