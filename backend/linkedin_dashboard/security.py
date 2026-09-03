from __future__ import annotations

from http import HTTPStatus

from starlette.types import ASGIApp, Receive, Scope, Send

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
