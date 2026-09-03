from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_DROP_KEYS = {
    "access_token",
    "authorization",
    "browser_profile_path",
    "cookie_path",
    "db_path",
    "mcp_url",
    "portable_cookie_path",
    "proxy_password",
    "runtime",
    "source_profile_dir",
}

_MALFORMED_JSON_BODY = b'{"detail":"Response could not be safely serialized"}'


def _is_json_media_type(content_type: str) -> bool:
    media_type = content_type.partition(";")[0].strip().casefold()
    return media_type == "application/json" or (
        media_type.startswith("application/") and media_type.endswith("+json")
    )


def sanitize_for_frontend(value: Any) -> Any:
    """Recursively remove process-local diagnostics and path material."""
    if isinstance(value, dict):
        return {
            key: sanitize_for_frontend(child)
            for key, child in value.items()
            if str(key).casefold() not in _DROP_KEYS
        }
    if isinstance(value, list):
        return [sanitize_for_frontend(child) for child in value]
    if isinstance(value, tuple):
        return [sanitize_for_frontend(child) for child in value]
    if isinstance(value, str):
        home = str(Path.home())
        sanitized = value.replace(home, "[redacted-home]")
        return sanitized.replace(".linkedin-mcp", "[redacted-profile]")
    return value


class PrivacyFilterMiddleware:
    """Sanitize every JSON response at the final backend boundary."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start: Message | None = None
        chunks: list[bytes] = []
        filter_json = False

        async def capture(message: Message) -> None:
            nonlocal filter_json, start
            if message["type"] == "http.response.start":
                start = message
                headers = list(message.get("headers", []))
                content_type = next(
                    (
                        value.decode("latin-1")
                        for key, value in headers
                        if key.decode("latin-1").casefold() == "content-type"
                    ),
                    "",
                )
                filter_json = _is_json_media_type(content_type)
                if not filter_json:
                    await send(message)
                return
            if message["type"] != "http.response.body" or start is None:
                await send(message)
                return
            if not filter_json:
                await send(message)
                return

            chunks.append(message.get("body", b""))
            if message.get("more_body", False):
                return

            headers = list(start.get("headers", []))
            body = b"".join(chunks)
            try:
                payload = json.loads(body)
                body = json.dumps(
                    sanitize_for_frontend(payload),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            except (UnicodeDecodeError, json.JSONDecodeError):
                body = _MALFORMED_JSON_BODY
                start["status"] = 500
                headers = [
                    (key, value)
                    for key, value in headers
                    if key.decode("latin-1").casefold() != "content-type"
                ]
                headers.append((b"content-type", b"application/json"))

            headers = [
                (key, value)
                for key, value in headers
                if key.decode("latin-1").casefold() != "content-length"
            ]
            headers.append((b"content-length", str(len(body)).encode("ascii")))
            start["headers"] = headers
            await send(start)
            await send({"type": "http.response.body", "body": body})

        await self.app(scope, receive, capture)
