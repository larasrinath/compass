from __future__ import annotations

import json
import re
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
_CREDENTIAL_URL = re.compile(
    r"(?P<scheme>[a-z][a-z0-9+.-]*://)[^/@\s]+@",
    flags=re.IGNORECASE,
)
_SSE_BOUNDARY = re.compile(rb"\r\n\r\n|\n\n|\r\r")


def _is_json_media_type(content_type: str) -> bool:
    media_type = content_type.partition(";")[0].strip().casefold()
    return media_type == "application/json" or (
        media_type.startswith("application/") and media_type.endswith("+json")
    )


def _redact_string(value: str) -> str:
    sanitized = _CREDENTIAL_URL.sub(r"\g<scheme>[redacted]@", value)
    sanitized = sanitized.replace(str(Path.home()), "[redacted-home]")
    return sanitized.replace(".linkedin-mcp", "[redacted-profile]")


def _redact_key(value: Any) -> Any:
    return _redact_string(value) if isinstance(value, str) else value


def sanitize_for_frontend(value: Any) -> Any:
    """Recursively remove process-local diagnostics and path material."""
    if isinstance(value, dict):
        return {
            _redact_key(key): sanitize_for_frontend(child)
            for key, child in value.items()
            if str(key).casefold() not in _DROP_KEYS
        }
    if isinstance(value, list):
        return [sanitize_for_frontend(child) for child in value]
    if isinstance(value, tuple):
        return [sanitize_for_frontend(child) for child in value]
    if isinstance(value, str):
        return _redact_string(value)
    return value


def _sanitize_sse_event(event: bytes, *, terminated: bool) -> bytes:
    try:
        text = event.decode("utf-8")
    except UnicodeDecodeError:
        safe = "event: error\ndata: Response could not be safely serialized"
        return (safe + ("\n\n" if terminated else "")).encode()

    output: list[str] = []
    data: list[str] = []
    data_position: int | None = None
    for line in text.splitlines():
        if line == "data" or line.startswith("data:"):
            if data_position is None:
                data_position = len(output)
            value = line[5:] if line.startswith("data:") else ""
            data.append(value[1:] if value.startswith(" ") else value)
        else:
            output.append(_redact_string(line))

    if data_position is not None:
        payload = "\n".join(data)
        try:
            value = json.loads(payload)
        except json.JSONDecodeError:
            if payload.lstrip().startswith(("{", "[")):
                safe_payload = _MALFORMED_JSON_BODY.decode()
            else:
                safe_payload = _redact_string(payload)
        else:
            safe_payload = json.dumps(
                sanitize_for_frontend(value),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        safe_data = [f"data: {line}" for line in safe_payload.split("\n")]
        output[data_position:data_position] = safe_data or ["data:"]

    suffix = "\n\n" if terminated else ""
    return ("\n".join(output) + suffix).encode("utf-8")


class PrivacyFilterMiddleware:
    """Sanitize every structured frontend response at the final boundary."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start: Message | None = None
        chunks: list[bytes] = []
        response_kind = "passthrough"
        sse_buffer = b""

        async def capture(message: Message) -> None:
            nonlocal response_kind, sse_buffer, start
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
                media_type = content_type.partition(";")[0].strip().casefold()
                if _is_json_media_type(content_type):
                    response_kind = "json"
                elif media_type == "text/event-stream":
                    response_kind = "sse"
                    message["headers"] = [
                        (key, value)
                        for key, value in headers
                        if key.decode("latin-1").casefold() != "content-length"
                    ]
                    await send(message)
                else:
                    await send(message)
                return
            if message["type"] != "http.response.body" or start is None:
                await send(message)
                return
            if response_kind == "passthrough":
                await send(message)
                return
            if response_kind == "sse":
                sse_buffer += message.get("body", b"")
                output: list[bytes] = []
                while boundary := _SSE_BOUNDARY.search(sse_buffer):
                    output.append(
                        _sanitize_sse_event(
                            sse_buffer[: boundary.start()], terminated=True
                        )
                    )
                    sse_buffer = sse_buffer[boundary.end() :]
                if not message.get("more_body", False) and sse_buffer:
                    output.append(_sanitize_sse_event(sse_buffer, terminated=False))
                    sse_buffer = b""
                await send(
                    {
                        **message,
                        "body": b"".join(output),
                    }
                )
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
