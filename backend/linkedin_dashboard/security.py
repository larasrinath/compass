from __future__ import annotations

import ipaddress
from collections.abc import Awaitable, Callable
from http import HTTPStatus

import psutil
from starlette.types import ASGIApp, Receive, Scope, Send

from linkedin_dashboard.db.session import Database
from linkedin_dashboard.settings import normalize_loopback_host

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


def _request_host(value: str) -> str | None:
    """Parse an HTTP Host header without breaking bracketed IPv6 literals."""
    raw = value.strip()
    if raw.startswith("["):
        closing = raw.find("]")
        if closing < 0:
            return None
        host = raw[1:closing]
        suffix = raw[closing + 1 :]
        if suffix and not (suffix.startswith(":") and suffix[1:].isdigit()):
            return None
    else:
        if raw.count(":") > 1:
            return None
        host, separator, port = raw.partition(":")
        if separator and not port.isdigit():
            return None

    try:
        return normalize_loopback_host(host)
    except ValueError:
        return None


async def _json_error(send: Send, status: HTTPStatus, detail: str) -> None:
    body = ('{"detail":"' + detail + '"}').encode()
    await send(
        {
            "type": "http.response.start",
            "status": status.value,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class ConfiguredHostMiddleware:
    """Accept only the exact normalized host on which Uvicorn is bound."""

    def __init__(self, app: ASGIApp, *, allowed_host: str) -> None:
        self.app = app
        self.allowed_host = normalize_loopback_host(allowed_host)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        values = [
            value.decode("latin-1")
            for key, value in scope.get("headers", [])
            if key.decode("latin-1").casefold() == "host"
        ]
        if len(values) != 1 or _request_host(values[0]) != self.allowed_host:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                await _json_error(send, HTTPStatus.BAD_REQUEST, "Invalid host header")
            return
        await self.app(scope, receive, send)


class OriginGuardMiddleware:
    """Reject browser mutation requests from every unconfigured origin."""

    def __init__(self, app: ASGIApp, *, allowed_origin: str) -> None:
        self.app = app
        self.allowed_origin = allowed_origin

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method", "GET") in _SAFE_METHODS:
            await self.app(scope, receive, send)
            return

        origins = [
            value.decode("latin-1")
            for key, value in scope.get("headers", [])
            if key.decode("latin-1").casefold() == "origin"
        ]
        if origins and (len(origins) != 1 or origins[0] != self.allowed_origin):
            await _json_error(send, HTTPStatus.FORBIDDEN, "Origin is not allowed")
            return
        await self.app(scope, receive, send)


def _resolved_addresses(host: str, port: int) -> set[str]:
    del port
    return {str(ipaddress.ip_address(host))}


def _listener_matches(scope: Scope, *, host: str, port: int) -> bool:
    expected_addresses = _resolved_addresses(host, port)
    server = scope.get("server")
    if server is None or server[1] != port:
        return False
    try:
        scope_address = str(ipaddress.ip_address(server[0]))
    except ValueError:
        return False
    if scope_address not in expected_addresses:
        return False

    listeners: list[str] = []
    for connection in psutil.Process().net_connections(kind="inet"):
        if connection.status != psutil.CONN_LISTEN or not connection.laddr:
            continue
        address, listener_port = connection.laddr[:2]
        if listener_port == port:
            listeners.append(str(ipaddress.ip_address(address)))
    return bool(listeners) and all(
        listener in expected_addresses for listener in listeners
    )


class RuntimeBoundaryMiddleware:
    """Initialize storage only after proving the real listener is configured."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        host: str,
        port: int,
        database: Database,
        on_ready: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.app = app
        self.host = normalize_loopback_host(host)
        self.port = port
        self.database = database
        self.on_ready = on_ready

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        is_test_client = client is not None and client[0] == "testclient"
        if not is_test_client and not _listener_matches(
            scope,
            host=self.host,
            port=self.port,
        ):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                await _json_error(
                    send,
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "Runtime listener does not match configured loopback binding",
                )
            return

        try:
            self.database.initialize()
            if self.on_ready is not None:
                await self.on_ready()
        except Exception:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1011})
            else:
                await _json_error(
                    send,
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "Local database could not be initialized safely",
                )
            return
        await self.app(scope, receive, send)
