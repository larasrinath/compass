from __future__ import annotations

import json

from linkedin_dashboard.mcp.envelope import parse_response_envelope
from mcp.types import CallToolResult


def test_full_sdk_envelope_and_unknown_keys_round_trip_without_loss() -> None:
    raw = CallToolResult.model_validate(
        {
            "content": [{"type": "text", "text": '{"url":"/in/alice/"}'}],
            "structuredContent": {
                "url": "https://www.linkedin.com/in/alice/",
                "sections": {"main_profile": "Alice\nEngineer"},
                "server_extension": {"nested": [1, True, None]},
            },
            "isError": False,
            "_meta": {"progressToken": "p-1"},
            "futureProtocolField": {"kept": "yes"},
        }
    )

    envelope = parse_response_envelope(raw)
    restored = json.loads(envelope.as_json())

    assert restored["structuredContent"]["server_extension"] == {
        "nested": [1, True, None]
    }
    assert restored["futureProtocolField"] == {"kept": "yes"}
    assert restored["_meta"] == {"progressToken": "p-1"}
    assert restored["content"][0]["text"] == '{"url":"/in/alice/"}'


def test_deterministic_serialization_is_key_order_independent() -> None:
    first = parse_response_envelope(
        {
            "content": [],
            "structuredContent": {"z": 1, "a": {"y": 2, "b": 3}},
            "isError": False,
        }
    )
    second = parse_response_envelope(
        {
            "isError": False,
            "structuredContent": {"a": {"b": 3, "y": 2}, "z": 1},
            "content": [],
        }
    )

    assert first.as_json() == second.as_json()


def test_legacy_text_and_single_result_wrapper_are_supported() -> None:
    envelope = parse_response_envelope(
        {
            "content": [
                {
                    "type": "text",
                    "text": '{"result":{"url":"u","sections":{"about":"raw"}}}',
                }
            ],
            "isError": False,
        }
    )

    assert envelope.result_payload() == {"url": "u", "sections": {"about": "raw"}}
