from __future__ import annotations

import re
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from linkedin_dashboard.mcp.envelope import MCPResponseEnvelope
from linkedin_dashboard.mcp.errors import (
    MCPClientError,
    MCPErrorDetails,
    error_details,
    response_error_details,
)

NetworkToken = Literal["F", "S", "O"]


class Reference(BaseModel):
    model_config = ConfigDict(extra="allow")

    kind: str
    url: str | None = None
    text: str | None = None
    context: str | None = None
    value: str | None = None


class SectionError(BaseModel):
    model_config = ConfigDict(extra="allow")

    error_type: str
    error_message: str | None = None


class ProfilePayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    url: str
    sections: dict[str, str]
    references: dict[str, list[Reference]] = Field(default_factory=dict)
    section_errors: dict[str, SectionError] = Field(default_factory=dict)
    unknown_sections: list[str] = Field(default_factory=list)


class PersonProfilePayload(ProfilePayload):
    profile_urn: str | None = None


class ToolResult[PayloadT: BaseModel](BaseModel):
    model_config = ConfigDict(extra="forbid")

    response: MCPResponseEnvelope
    payload: PayloadT | None
    error: MCPErrorDetails | None = None


class MessagePayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    url: str
    status: str
    message: str
    recipient_selected: bool
    sent: bool


type SearchPeopleResult = ToolResult[ProfilePayload]
type PersonProfileResult = ToolResult[PersonProfilePayload]
type CompanyProfileResult = ToolResult[ProfilePayload]
type SendMessageResult = ToolResult[MessagePayload]


class MCPCaller(Protocol):
    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> MCPResponseEnvelope: ...


class LinkedInReadTools:
    """Typed argument and result boundary for the three M1 read-only tools."""

    def __init__(self, client: MCPCaller) -> None:
        self._client = client

    async def search_people(
        self,
        keywords: str,
        *,
        location: str | None = None,
        network: list[NetworkToken] | None = None,
        current_company: str | None = None,
    ) -> SearchPeopleResult:
        invalid = [token for token in network or [] if token not in {"F", "S", "O"}]
        if invalid:
            raise ValueError(
                f"Invalid network token(s) {invalid!r}; expected any of ['F', 'S', 'O']"
            )
        if current_company and re.fullmatch(r"[0-9]+", current_company) is None:
            raise ValueError(
                "current_company must be a numeric LinkedIn company URN id "
                "(e.g. '1115' for SAP); plain-text company names are silently "
                "ignored by LinkedIn. Look up the URN via get_company_profile "
                '-> references["about"].'
            )
        arguments: dict[str, object] = {"keywords": keywords}
        _include(arguments, "location", location)
        _include(arguments, "network", network)
        _include(arguments, "current_company", current_company)
        response = await self._client.call_tool("search_people", arguments)
        return _parse_result(response, ProfilePayload)

    async def get_person_profile(
        self,
        linkedin_username: str,
        *,
        sections: str | None = None,
        max_scrolls: int | None = None,
    ) -> PersonProfileResult:
        arguments: dict[str, object] = {"linkedin_username": linkedin_username}
        _include(arguments, "sections", sections)
        _include(arguments, "max_scrolls", max_scrolls)
        response = await self._client.call_tool("get_person_profile", arguments)
        return _parse_result(response, PersonProfilePayload)

    async def get_company_profile(
        self,
        company_name: str,
        *,
        sections: str | None = None,
    ) -> CompanyProfileResult:
        arguments: dict[str, object] = {"company_name": company_name}
        _include(arguments, "sections", sections)
        response = await self._client.call_tool("get_company_profile", arguments)
        return _parse_result(response, ProfilePayload)


class LinkedInMessagingTools:
    """Transport-only messaging wrapper; policy and confirmation live elsewhere."""

    def __init__(self, client: MCPCaller) -> None:
        self._client = client

    async def send_message(
        self,
        linkedin_username: str,
        message: str,
        confirm_send: bool,
        *,
        profile_urn: str | None = None,
    ) -> SendMessageResult:
        arguments: dict[str, object] = {
            "linkedin_username": linkedin_username,
            "message": message,
            "confirm_send": confirm_send,
        }
        _include(arguments, "profile_urn", profile_urn)
        response = await self._client.call_tool("send_message", arguments)
        return _parse_result(response, MessagePayload)


def _include(arguments: dict[str, object], name: str, value: object | None) -> None:
    if value is not None:
        arguments[name] = value


def _parse_result[PayloadT: BaseModel](
    response: MCPResponseEnvelope,
    payload_type: type[PayloadT],
) -> ToolResult[PayloadT]:
    details = response_error_details(response)
    if response.is_error:
        return ToolResult[PayloadT](response=response, payload=None, error=details)
    try:
        parsed = payload_type.model_validate(response.result_payload())
    except ValueError as error:
        raise MCPClientError(
            error_details(error, partial_payload=response.as_dict())
        ) from error
    return ToolResult[PayloadT](response=response, payload=parsed, error=details)
