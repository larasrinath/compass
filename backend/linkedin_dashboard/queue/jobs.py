from __future__ import annotations

import json
import re
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

MAX_JOB_PAYLOAD_BYTES = 64 * 1024


class JobKind(StrEnum):
    LIST_TOOLS = "list_tools"
    SEARCH_PEOPLE = "search_people"
    GET_PERSON_PROFILE = "get_person_profile"
    GET_COMPANY_PROFILE = "get_company_profile"


class _StrictPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ListToolsPayload(_StrictPayload):
    kind: Literal[JobKind.LIST_TOOLS] = JobKind.LIST_TOOLS


class SearchPeoplePayload(_StrictPayload):
    kind: Literal[JobKind.SEARCH_PEOPLE] = JobKind.SEARCH_PEOPLE
    keywords: str = Field(min_length=1, max_length=500)
    location: str | None = Field(default=None, max_length=200)
    network: list[Literal["F", "S", "O"]] | None = Field(default=None, max_length=3)
    current_company: str | None = Field(default=None, max_length=32)
    search_run_id: str | None = Field(default=None, min_length=1, max_length=36)
    page: int | None = Field(default=None, ge=1, le=1000, strict=True)

    @field_validator("current_company")
    @classmethod
    def numeric_company_urn(cls, value: str | None) -> str | None:
        if value is not None and re.fullmatch(r"[0-9]+", value) is None:
            raise ValueError("current_company must be a numeric company URN id")
        return value


class PersonProfilePayload(_StrictPayload):
    kind: Literal[JobKind.GET_PERSON_PROFILE] = JobKind.GET_PERSON_PROFILE
    linkedin_username: str = Field(min_length=1, max_length=200)
    sections: list[str] = Field(min_length=1, max_length=32)
    max_scrolls: int | None = Field(default=None, ge=1, le=50)
    parent_job_id: str | None = Field(default=None, max_length=36)

    @field_validator("sections")
    @classmethod
    def unique_sections(cls, value: list[str]) -> list[str]:
        if any(not section or len(section) > 64 for section in value):
            raise ValueError(
                "sections must contain non-empty names up to 64 characters"
            )
        if len(set(value)) != len(value):
            raise ValueError("sections must not contain duplicates")
        return value


class CompanyProfilePayload(_StrictPayload):
    kind: Literal[JobKind.GET_COMPANY_PROFILE] = JobKind.GET_COMPANY_PROFILE
    company_name: str = Field(min_length=1, max_length=200)
    sections: list[str] | None = Field(default=None, min_length=1, max_length=32)
    company_lookup_id: str | None = Field(default=None, min_length=1, max_length=36)


type JobPayload = Annotated[
    ListToolsPayload
    | SearchPeoplePayload
    | PersonProfilePayload
    | CompanyProfilePayload,
    Field(discriminator="kind"),
]

_PAYLOAD_ADAPTER = TypeAdapter(JobPayload)


def validate_payload(kind: JobKind | str, value: dict[str, Any]) -> JobPayload:
    try:
        normalized_kind = JobKind(kind)
    except ValueError as error:
        raise ValueError("job kind is not allowlisted") from error
    candidate = {**value, "kind": normalized_kind}
    encoded = json.dumps(
        candidate,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    if len(encoded) > MAX_JOB_PAYLOAD_BYTES:
        raise ValueError("job payload exceeds the 64 KiB limit")
    return _PAYLOAD_ADAPTER.validate_python(candidate)


def persisted_payload(payload: JobPayload) -> dict[str, Any]:
    return payload.model_dump(mode="json", exclude={"kind"}, exclude_none=True)


def max_attempts_for(kind: JobKind) -> int:
    return 1 if kind is JobKind.LIST_TOOLS else 2


def navigation_cost(payload: JobPayload) -> int:
    if isinstance(payload, ListToolsPayload):
        return 0
    if isinstance(payload, PersonProfilePayload):
        # main_profile is an unavoidable navigation in every external call,
        # including a missing-section continuation.
        return 1 + sum(section != "main_profile" for section in payload.sections)
    if isinstance(payload, CompanyProfilePayload):
        return len(payload.sections) if payload.sections is not None else 1
    return 1


def tool_arguments(payload: JobPayload) -> tuple[str, dict[str, Any]]:
    if isinstance(payload, ListToolsPayload):
        raise ValueError("list_tools is not a tools/call operation")
    arguments = payload.model_dump(
        mode="json",
        exclude={"kind", "parent_job_id", "search_run_id", "company_lookup_id"},
        exclude_none=True,
    )
    sections = arguments.get("sections")
    if isinstance(sections, list):
        explicit_sections = [
            section for section in sections if section != "main_profile"
        ]
        if explicit_sections:
            arguments["sections"] = ",".join(explicit_sections)
        else:
            arguments.pop("sections")
    return payload.kind.value, arguments


def missing_profile_sections(
    payload: JobPayload, result_payload: dict[str, Any]
) -> list[str]:
    if not isinstance(payload, PersonProfilePayload):
        return []
    errors = result_payload.get("section_errors")
    if not isinstance(errors, dict):
        return []
    # The extractor aborts in requested order at the first rate-limit marker.
    # An attempted empty section may be absent from both result maps, so
    # requested-minus-returned would replay work that was already attempted.
    # main_profile is implicit but first in the upstream declaration order. If
    # it rate-limits, every explicit section remains missing.
    explicit_sections = [
        section for section in payload.sections if section != "main_profile"
    ]
    effective_sections = ["main_profile", *explicit_sections]
    for index, name in enumerate(effective_sections):
        error = errors.get(name)
        if (
            isinstance(error, dict)
            and str(error.get("error_type", "")).casefold() == "rate_limit"
        ):
            return [
                section
                for section in effective_sections[index:]
                if section != "main_profile"
            ]
    return []


def unattempted_profile_navigation_count(
    payload: JobPayload, result_payload: dict[str, Any]
) -> int:
    """Count reserved suffix sections skipped after the rate-limit sentinel."""
    if not isinstance(payload, PersonProfilePayload):
        return 0
    errors = result_payload.get("section_errors")
    if not isinstance(errors, dict):
        return 0
    effective = [
        "main_profile",
        *(section for section in payload.sections if section != "main_profile"),
    ]
    for index, name in enumerate(effective):
        error = errors.get(name)
        if (
            isinstance(error, dict)
            and str(error.get("error_type", "")).casefold() == "rate_limit"
        ):
            return len(effective) - index - 1
    return 0
