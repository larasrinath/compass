from __future__ import annotations

from typing import Any, cast

import httpx
import pytest
from linkedin_dashboard.mcp.envelope import parse_response_envelope
from linkedin_dashboard.mcp.errors import ErrorClass, classify, response_error_details
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData


def _tool_error(text: str):
    return parse_response_envelope(
        {"content": [{"type": "text", "text": text}], "isError": True}
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (_tool_error("Session expired. Run with --login"), ErrorClass.AUTH_REQUIRED),
        (
            _tool_error("Another LinkedIn MCP client is currently using the browser."),
            ErrorClass.BROWSER_BUSY,
        ),
        (
            _tool_error("LinkedIn browser setup was not ready: install pending"),
            ErrorClass.BROWSER_SETUP,
        ),
        (_tool_error("Rate limit detected. Wait 300 seconds"), ErrorClass.RATE_LIMIT),
        (
            _tool_error("Pass the /company/ slug, not the full reference."),
            ErrorClass.INVALID_REFERENCE,
        ),
        (
            _tool_error("Profile not found. Check the profile URL"),
            ErrorClass.PROFILE_NOT_FOUND,
        ),
        (
            McpError(ErrorData(code=-32000, message="Tool 'x' execution timed out")),
            ErrorClass.TIMEOUT,
        ),
        (
            httpx.ConnectError("All connection attempts failed"),
            ErrorClass.TRANSPORT,
        ),
        (_tool_error("Error calling tool 'x'"), ErrorClass.UNKNOWN),
    ],
)
def test_all_nine_planned_error_classes(value, expected: ErrorClass) -> None:
    assert classify(value) is expected


def test_client_408_timeout_is_classified() -> None:
    error = McpError(
        ErrorData(code=408, message="Timed out while waiting for a response")
    )
    assert classify(error) is ErrorClass.TIMEOUT


def test_httpx_timeout_is_not_misclassified_as_transport() -> None:
    assert classify(httpx.ReadTimeout("late")) is ErrorClass.TIMEOUT


def test_rate_limit_in_successful_partial_payload_is_structural() -> None:
    response = parse_response_envelope(
        {
            "content": [],
            "structuredContent": {
                "url": "https://www.linkedin.com/in/alice/",
                "sections": {"main_profile": "Alice"},
                "section_errors": {
                    "experience": {
                        "error_type": "rate_limit",
                        "error_message": "Please wait",
                        "runtime": {"source_profile_dir": "/private/profile"},
                    }
                },
            },
            "isError": False,
        }
    )

    details = response_error_details(response, correlation_id="corr-1")
    assert details is not None
    assert details.error_class is ErrorClass.RATE_LIMIT
    assert details.correlation_id == "corr-1"
    assert "runtime" not in str(details.partial_payload).casefold()
    assert response.structured_content is not None
    payload = cast(dict[str, Any], response.structured_content)
    assert "runtime" in payload["section_errors"]["experience"]
