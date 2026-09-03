from __future__ import annotations

from typing import Any

import pytest
from linkedin_dashboard.mcp.envelope import MCPResponseEnvelope, parse_response_envelope
from linkedin_dashboard.mcp.errors import ErrorClass
from linkedin_dashboard.mcp.tools import LinkedInReadTools


class RecordingClient:
    def __init__(self, response: MCPResponseEnvelope | None = None) -> None:
        self.response = response or parse_response_envelope(
            {
                "content": [],
                "structuredContent": {"url": "u", "sections": {}},
                "isError": False,
            }
        )
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> MCPResponseEnvelope:
        self.calls.append((name, arguments))
        return self.response


@pytest.mark.asyncio
async def test_search_people_uses_exact_arguments_and_omits_none() -> None:
    client = RecordingClient()
    tools = LinkedInReadTools(client)

    await tools.search_people("AI engineer")
    await tools.search_people(
        "AI engineer",
        location="Chicago",
        network=["F", "S"],
        current_company="1115",
    )

    assert client.calls == [
        ("search_people", {"keywords": "AI engineer"}),
        (
            "search_people",
            {
                "keywords": "AI engineer",
                "location": "Chicago",
                "network": ["F", "S"],
                "current_company": "1115",
            },
        ),
    ]


@pytest.mark.asyncio
async def test_profile_and_company_wrappers_use_exact_server_names() -> None:
    client = RecordingClient()
    tools = LinkedInReadTools(client)

    await tools.get_person_profile(
        "alice", sections="experience,skills", max_scrolls=20
    )
    await tools.get_company_profile("acme")

    assert client.calls == [
        (
            "get_person_profile",
            {
                "linkedin_username": "alice",
                "sections": "experience,skills",
                "max_scrolls": 20,
            },
        ),
        ("get_company_profile", {"company_name": "acme"}),
    ]


@pytest.mark.asyncio
async def test_partial_payload_and_unknown_fields_are_typed_without_loss() -> None:
    response = parse_response_envelope(
        {
            "content": [],
            "structuredContent": {
                "url": "https://www.linkedin.com/in/alice/",
                "sections": {"main_profile": "Alice"},
                "profile_urn": "urn:li:fsd_profile:123",
                "unknown_sections": ["future"],
                "references": {
                    "main_profile": [
                        {
                            "kind": "person",
                            "url": "/in/alice/",
                            "future_reference_key": 7,
                        }
                    ]
                },
                "section_errors": {
                    "experience": {
                        "error_type": "rate_limit",
                        "runtime": {"hostname": "private-host"},
                    }
                },
                "future_payload_key": {"kept": True},
            },
            "isError": False,
            "futureProtocolField": "also-kept",
        }
    )
    tools = LinkedInReadTools(RecordingClient(response))

    result = await tools.get_person_profile("alice", sections="experience")

    assert result.payload is not None
    assert result.payload.profile_urn == "urn:li:fsd_profile:123"
    assert result.payload.model_extra == {"future_payload_key": {"kept": True}}
    assert result.payload.references["main_profile"][0].model_extra == {
        "future_reference_key": 7
    }
    assert result.response.model_extra == {"futureProtocolField": "also-kept"}
    assert result.error is not None
    assert result.error.error_class is ErrorClass.RATE_LIMIT
    assert "runtime" not in str(result.error.partial_payload).casefold()
    assert "runtime" in result.response.as_json()


@pytest.mark.asyncio
async def test_tool_error_remains_a_full_envelope_with_safe_classification() -> None:
    response = parse_response_envelope(
        {
            "content": [
                {"type": "text", "text": "Profile not found. Check the profile URL."}
            ],
            "isError": True,
            "futureProtocolField": {"kept": True},
        }
    )
    tools = LinkedInReadTools(RecordingClient(response))

    result = await tools.get_person_profile("missing")

    assert result.payload is None
    assert result.error is not None
    assert result.error.error_class is ErrorClass.PROFILE_NOT_FOUND
    assert result.response.model_extra == {"futureProtocolField": {"kept": True}}


def test_no_send_wrapper_is_exposed() -> None:
    assert not hasattr(LinkedInReadTools, "send_message")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"network": ["X"]},
        {"current_company": "SAP"},
        {"current_company": "\u0661\u0661\u0661\u0665"},
    ],
)
async def test_invalid_search_facets_are_rejected_before_the_call(kwargs) -> None:
    client = RecordingClient()
    tools = LinkedInReadTools(client)

    with pytest.raises(ValueError):
        await tools.search_people("engineer", **kwargs)

    assert client.calls == []
