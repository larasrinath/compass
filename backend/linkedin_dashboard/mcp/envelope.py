from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

JsonObject = dict[str, JsonValue]


class MalformedMCPResponse(ValueError):
    """The server response could not be represented as a valid MCP envelope."""

    def __init__(
        self,
        message: str,
        *,
        partial_payload: JsonObject | None = None,
    ) -> None:
        super().__init__(message)
        self.partial_payload = partial_payload


class MCPResponseEnvelope(BaseModel):
    """Lossless, JSON-safe view of a ``tools/call`` protocol result."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    content: list[JsonObject]
    structured_content: JsonObject | None = Field(
        default=None,
        alias="structuredContent",
    )
    is_error: bool = Field(default=False, alias="isError")
    metadata: JsonObject | None = Field(default=None, alias="_meta")

    def as_dict(self) -> JsonObject:
        return self.model_dump(mode="json", by_alias=True)

    def as_json(self) -> str:
        """Return a stable representation suitable for hashing and durable storage."""
        return json.dumps(
            self.as_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    def result_payload(self) -> JsonObject:
        """Extract the server's object result without discarding the MCP envelope."""
        if self.structured_content is not None:
            payload = self.structured_content
            # FastMCP 3.x normally unwraps a single object result. Accept the
            # older defensive wrapper while keeping the original envelope intact.
            wrapped = payload.get("result")
            if len(payload) == 1 and isinstance(wrapped, dict):
                return wrapped
            return payload

        for block in self.content:
            text = block.get("text")
            if block.get("type") != "text" or not isinstance(text, str):
                continue
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                wrapped = value.get("result")
                if len(value) == 1 and isinstance(wrapped, dict):
                    return wrapped
                return value

        if self.is_error:
            return {}
        raise MalformedMCPResponse(
            "MCP tool response did not contain an object result",
            partial_payload=self.as_dict(),
        )


def parse_response_envelope(value: object) -> MCPResponseEnvelope:
    """Normalize an SDK model or mapping without losing protocol extensions."""
    try:
        raw = _as_json_object(value)
        return MCPResponseEnvelope.model_validate(raw)
    except MalformedMCPResponse:
        raise
    except (TypeError, ValueError, ValidationError) as error:
        partial = _best_effort_object(value)
        raise MalformedMCPResponse(
            "MCP tool response was malformed",
            partial_payload=partial,
        ) from error


def serialize_json_object(value: object) -> JsonObject:
    """Public deterministic JSON-safety boundary used by tests and persistence."""
    return _as_json_object(value)


def _as_json_object(value: object) -> JsonObject:
    if isinstance(value, BaseModel):
        candidate: Any = value.model_dump(mode="json", by_alias=True)
    elif isinstance(value, Mapping):
        candidate = dict(value)
    else:
        raise TypeError("MCP response must be an object")

    if not isinstance(candidate, dict):
        raise TypeError("MCP response must serialize to an object")
    try:
        # A round-trip rejects Python-only values and provides a detached snapshot.
        normalized = json.loads(
            json.dumps(candidate, ensure_ascii=False, allow_nan=False, sort_keys=True)
        )
    except (TypeError, ValueError) as error:
        raise TypeError("MCP response is not JSON-safe") from error
    if not isinstance(normalized, dict):  # pragma: no cover - guarded above
        raise TypeError("MCP response must serialize to an object")
    return normalized


def _best_effort_object(value: object) -> JsonObject | None:
    try:
        return _as_json_object(value)
    except (TypeError, ValueError):
        return None
