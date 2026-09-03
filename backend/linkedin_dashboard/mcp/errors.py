from __future__ import annotations

import asyncio
from enum import StrEnum
from typing import Any

import httpx
from mcp.shared.exceptions import McpError
from pydantic import BaseModel, ConfigDict, JsonValue

from linkedin_dashboard.api._filters import sanitize_for_frontend
from linkedin_dashboard.correlation import current_correlation_id
from linkedin_dashboard.mcp.envelope import MCPResponseEnvelope


class ErrorClass(StrEnum):
    AUTH_REQUIRED = "AUTH_REQUIRED"
    BROWSER_BUSY = "BROWSER_BUSY"
    BROWSER_SETUP = "BROWSER_SETUP"
    RATE_LIMIT = "RATE_LIMIT"
    INVALID_REFERENCE = "INVALID_REFERENCE"
    PROFILE_NOT_FOUND = "PROFILE_NOT_FOUND"
    TIMEOUT = "TIMEOUT"
    TRANSPORT = "TRANSPORT"
    UNKNOWN = "UNKNOWN"


_SAFE_MESSAGES: dict[ErrorClass, str] = {
    ErrorClass.AUTH_REQUIRED: "LinkedIn authentication is required on the MCP server.",
    ErrorClass.BROWSER_BUSY: "The LinkedIn browser is currently in use.",
    ErrorClass.BROWSER_SETUP: "The LinkedIn browser setup is not ready.",
    ErrorClass.RATE_LIMIT: "LinkedIn rate-limited this request; partial data was kept.",
    ErrorClass.INVALID_REFERENCE: "The LinkedIn reference is invalid.",
    ErrorClass.PROFILE_NOT_FOUND: "The LinkedIn profile was not found.",
    ErrorClass.TIMEOUT: "The MCP operation timed out.",
    ErrorClass.TRANSPORT: "The local MCP server is unreachable.",
    ErrorClass.UNKNOWN: "The MCP operation failed.",
}


class MCPErrorDetails(BaseModel):
    """Frontend-safe classification with correlation and optional partial data."""

    model_config = ConfigDict(frozen=True)

    error_class: ErrorClass
    message: str
    correlation_id: str
    partial_payload: JsonValue | None = None


class MCPClientError(RuntimeError):
    """Safe exception raised for transport and malformed-response failures."""

    def __init__(self, details: MCPErrorDetails) -> None:
        super().__init__(details.message)
        self.details = details


def classify(value: BaseException | MCPResponseEnvelope | dict[str, Any]) -> ErrorClass:
    """Map an exception or protocol result to one of the nine planned classes."""
    if isinstance(value, McpError) and value.error.code == 408:
        return ErrorClass.TIMEOUT
    if isinstance(value, (TimeoutError, asyncio.TimeoutError, httpx.TimeoutException)):
        return ErrorClass.TIMEOUT
    if isinstance(
        value,
        (
            httpx.TransportError,
            ConnectionError,
            BrokenPipeError,
            ConnectionResetError,
        ),
    ):
        return ErrorClass.TRANSPORT

    payload = _payload(value)
    if _has_rate_limit(payload):
        return ErrorClass.RATE_LIMIT

    text = _error_text(value, payload).casefold()
    if _contains_any(
        text,
        (
            "session expired",
            "authentication failed",
            "authentication not found",
            "run with --login",
            "login browser window has been opened",
            "docker host login",
        ),
    ):
        return ErrorClass.AUTH_REQUIRED
    if _contains_any(
        text,
        (
            "another linkedin mcp client is currently using the browser",
            "another linkedin mcp client is using the browser",
            "browser is currently in use",
        ),
    ):
        return ErrorClass.BROWSER_BUSY
    if _contains_any(
        text,
        (
            "browser setup was not ready",
            "browser setup still in progress",
            "setup is not complete yet",
            "downloading the patchright chromium browser",
        ),
    ):
        return ErrorClass.BROWSER_SETUP
    if _contains_any(text, ("rate limit detected", "rate-limited", "rate limited")):
        return ErrorClass.RATE_LIMIT
    if _contains_any(
        text,
        (
            "not a linkedin public identifier",
            "pass the part after /in/",
            "pass the /company/ slug",
            "invalid reference",
        ),
    ):
        return ErrorClass.INVALID_REFERENCE
    if "profile not found" in text:
        return ErrorClass.PROFILE_NOT_FOUND
    if _contains_any(text, ("timed out", "timeout", "deadline exceeded")):
        return ErrorClass.TIMEOUT
    if _contains_any(
        text,
        (
            "connection refused",
            "connection reset",
            "connection closed",
            "session terminated",
            "all connection attempts failed",
        ),
    ):
        return ErrorClass.TRANSPORT
    return ErrorClass.UNKNOWN


def error_details(
    value: BaseException | MCPResponseEnvelope | dict[str, Any],
    *,
    partial_payload: JsonValue | None = None,
    correlation_id: str | None = None,
) -> MCPErrorDetails:
    error_class = classify(value)
    safe_partial = sanitize_for_frontend(partial_payload)
    return MCPErrorDetails(
        error_class=error_class,
        message=_SAFE_MESSAGES[error_class],
        correlation_id=correlation_id or current_correlation_id(),
        partial_payload=safe_partial,
    )


def response_error_details(
    response: MCPResponseEnvelope,
    *,
    correlation_id: str | None = None,
) -> MCPErrorDetails | None:
    error_class = classify(response)
    if not response.is_error and error_class is ErrorClass.UNKNOWN:
        return None
    return error_details(
        response,
        partial_payload=response.as_dict(),
        correlation_id=correlation_id,
    )


def _payload(value: object) -> dict[str, Any]:
    if isinstance(value, MCPResponseEnvelope):
        try:
            return value.result_payload()
        except ValueError:
            return {}
    if isinstance(value, dict):
        structured = value.get("structuredContent")
        return structured if isinstance(structured, dict) else value
    return {}


def _has_rate_limit(payload: dict[str, Any]) -> bool:
    section_errors = payload.get("section_errors")
    if not isinstance(section_errors, dict):
        return False
    return any(
        isinstance(section_error, dict)
        and str(section_error.get("error_type", "")).casefold() == "rate_limit"
        for section_error in section_errors.values()
    )


def _error_text(value: object, payload: dict[str, Any]) -> str:
    parts: list[str] = []
    if isinstance(value, BaseException):
        parts.append(str(value))
    elif isinstance(value, MCPResponseEnvelope):
        for block in value.content:
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    message = payload.get("message") or payload.get("error_message")
    if isinstance(message, str):
        parts.append(message)
    return "\n".join(parts)


def _contains_any(value: str, needles: tuple[str, ...]) -> bool:
    return any(needle in value for needle in needles)
