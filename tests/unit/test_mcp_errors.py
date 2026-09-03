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
        (
            _tool_error(
                "Session expired. Run with --login to create a new browser profile."
            ),
            ErrorClass.AUTH_REQUIRED,
        ),
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
            _tool_error(
                "That is a LinkedIn link but not a company page. Pass the "
                '/company/ slug, for example "microsoft".'
            ),
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


@pytest.mark.parametrize(
    "message",
    [
        "Session expired. A login browser window has been opened.",
        "Authentication failed. Run with --login to re-authenticate.",
        "Authentication not found. Run with --login to create a browser profile.",
        (
            "No valid LinkedIn session is available in Docker. Create one with "
            "the explicit --login --login-viewer Docker command."
        ),
        (
            "A LinkedIn login window is open and login is still in progress. "
            "This is not a failure. Complete the sign-in in the browser."
        ),
        (
            "No valid LinkedIn session is available yet. LinkedIn login is "
            "already in progress in a browser window."
        ),
        (
            "The shared LinkedIn browser has no usable session, and it cannot "
            "sign in by itself. Retry this tool."
        ),
        (
            "The shared LinkedIn browser's session stopped working, and it cannot "
            "sign in by itself. Retry this tool."
        ),
        (
            "LinkedIn login was not completed. Retry the tool call to reopen the "
            "browser and continue setup."
        ),
        "Could not retire the stale session: locked. No login was started.",
    ],
)
def test_current_source_pinned_auth_messages(message: str) -> None:
    assert classify(_tool_error(message)) is ErrorClass.AUTH_REQUIRED


@pytest.mark.parametrize(
    "message",
    [
        "Missing linkedin_username (the /in/ public identifier of the person).",
        (
            "That is a LinkedIn link but not a personal profile. Pass the /in/ "
            'public identifier of a person, for example "williamhgates".'
        ),
        (
            "That is not a LinkedIn public identifier. Pass the part after /in/ "
            'in a profile URL, for example "williamhgates".'
        ),
        (
            '"me" is LinkedIn\'s alias for the signed-in member, not a person you '
            "can look up. Call get_my_profile."
        ),
        (
            "That is a shortened LinkedIn link, and only a redirect resolves it. "
            "Open it and pass the /in/ public identifier the address contains."
        ),
    ],
)
def test_all_current_person_reference_corrections(message: str) -> None:
    assert classify(_tool_error(message)) is ErrorClass.INVALID_REFERENCE


@pytest.mark.parametrize(
    "message",
    [
        (
            "LinkedIn browser setup was not ready: mirror refused. Retry this "
            "tool to start a fresh background setup attempt."
        ),
        (
            "LinkedIn setup is not complete yet: the server is downloading the "
            "Patchright Chromium browser in the background."
        ),
        (
            "Patchright Chromium browser is missing. Run 'uv run patchright "
            "install chromium', or restart the server to auto-install."
        ),
        (
            "Chromium could not start because required system libraries are "
            "missing on this Linux host."
        ),
    ],
)
def test_current_source_pinned_setup_messages(message: str) -> None:
    assert classify(_tool_error(message)) is ErrorClass.BROWSER_SETUP


@pytest.mark.parametrize(
    "message",
    [
        "A report says authentication failed in an unrelated data field.",
        "The document mentions an invalid reference implementation.",
        "Run with --login was quoted in documentation.",
    ],
)
def test_domain_classification_does_not_use_broad_substrings(message: str) -> None:
    assert classify(_tool_error(message)) is ErrorClass.UNKNOWN


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
