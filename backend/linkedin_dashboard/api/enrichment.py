from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from linkedin_dashboard.api._filters import (
    preserve_provenance_text,
    redact_provenance_text,
)
from linkedin_dashboard.db.models import (
    Candidate,
    Job,
    ParsedField,
    ProfileFetch,
    ProfileSection,
    SectionError,
)
from linkedin_dashboard.services.enrichment import (
    EXTRA_PERSON_SECTIONS,
    EnrichmentService,
)

router = APIRouter(tags=["enrichment"])


def get_service(request: Request) -> EnrichmentService:
    return request.app.state.enrichment_service


class EnrichmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sections: list[str] = Field(default_factory=lambda: ["experience"], max_length=10)


class BatchEnrichmentInput(EnrichmentInput):
    candidate_ids: list[str] = Field(min_length=1, max_length=200)


class EnrichmentQueued(BaseModel):
    job_id: str
    estimated_navigations: int


@router.post(
    "/candidates/{candidate_id}/enrich",
    response_model=EnrichmentQueued,
    status_code=202,
)
async def enrich_candidate(
    candidate_id: str,
    payload: EnrichmentInput,
    service: Annotated[EnrichmentService, Depends(get_service)],
) -> EnrichmentQueued:
    try:
        job_id = await service.enqueue(candidate_id, payload.sections)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from error
    return EnrichmentQueued(
        job_id=job_id, estimated_navigations=1 + len(set(payload.sections))
    )


@router.post(
    "/candidates/enrich-batch",
    response_model=dict[str, Any],
    status_code=202,
)
async def enrich_batch(
    payload: BatchEnrichmentInput,
    service: Annotated[EnrichmentService, Depends(get_service)],
) -> dict[str, Any]:
    try:
        job_ids = await service.enqueue_batch(payload.candidate_ids, payload.sections)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from error
    return {
        "job_ids": job_ids,
        "estimated_navigations": len(job_ids) * (1 + len(set(payload.sections))),
    }


@router.get("/profile-sections", response_model=list[str])
def profile_sections() -> list[str]:
    return list(EXTRA_PERSON_SECTIONS)


@router.get("/candidates/{candidate_id}", response_model=dict[str, Any])
def candidate_detail(candidate_id: str, request: Request) -> dict[str, Any]:
    database = request.app.state.database
    with database.sessions() as session:
        candidate = session.get(Candidate, candidate_id)
        if candidate is None:
            raise HTTPException(404, "candidate does not exist")
        latest_sections = list(
            session.execute(
                select(ProfileSection)
                .where(ProfileSection.candidate_id == candidate_id)
                .order_by(ProfileSection.retrieved_at.desc(), ProfileSection.id.desc())
            ).scalars()
        )
        latest_by_name: dict[str, ProfileSection] = {}
        for section in latest_sections:
            latest_by_name.setdefault(section.section_name, section)
        fields = (
            list(
                session.scalars(
                    select(ParsedField)
                    .where(
                        ParsedField.candidate_id == candidate_id,
                        ParsedField.profile_section_id.in_(
                            [section.id for section in latest_by_name.values()]
                        ),
                    )
                    .order_by(ParsedField.section_name, ParsedField.span_start)
                )
            )
            if latest_by_name
            else []
        )
        fetches = list(
            session.scalars(
                select(ProfileFetch)
                .where(ProfileFetch.candidate_id == candidate_id)
                .order_by(ProfileFetch.started_at.desc())
            )
        )
        errors = list(
            session.scalars(
                select(SectionError)
                .where(SectionError.candidate_id == candidate_id)
                .order_by(SectionError.id)
            )
        )
        active = session.scalar(
            select(Job.id)
            .join(ProfileFetch, ProfileFetch.job_id == Job.id)
            .where(
                ProfileFetch.candidate_id == candidate_id,
                Job.state.in_(("pending", "queued", "running")),
            )
            .limit(1)
        )
        return {
            "id": candidate.id,
            "username": candidate.username,
            "profile_url": candidate.profile_url,
            "display_name": candidate.display_name,
            "profile_urn": candidate.profile_urn,
            "profile_urn_is_scored": False,
            "stage": candidate.stage,
            "retrieval_status": candidate.retrieval_status,
            "active_job_id": active,
            "available_sections": {
                name: {
                    "profile_section_id": section.id,
                    "retrieved_at": section.retrieved_at,
                    "char_len": section.char_len,
                    "field_count": sum(
                        field.profile_section_id == section.id for field in fields
                    ),
                }
                for name, section in latest_by_name.items()
            },
            "fields": [
                {
                    "id": field.id,
                    "field_key": field.field_key,
                    "value": field.value,
                    "section_name": field.section_name,
                    "profile_section_id": field.profile_section_id,
                    "span_start": field.span_start,
                    "span_end": field.span_end,
                    "snippet": field.snippet,
                    "origin": field.origin,
                }
                for field in fields
            ],
            "fetches": [
                {
                    "id": fetch.id,
                    "job_id": fetch.job_id,
                    "requested_sections": fetch.requested_sections,
                    "started_at": fetch.started_at,
                    "finished_at": fetch.finished_at,
                    "outcome": fetch.outcome,
                }
                for fetch in fetches
            ],
            "errors": [
                {
                    "section_name": error.section_name,
                    "error_type": error.error_type,
                    "error_message": error.error_message,
                    "extra": error.extra or {},
                }
                for error in errors
            ],
        }


def _overlaps(start: int, end: int, ranges: tuple[tuple[int, int], ...]) -> bool:
    return any(
        start < masked_end and masked_start < end for masked_start, masked_end in ranges
    )


@router.get(
    "/candidates/{candidate_id}/sections/{section_name}",
    response_model=dict[str, Any],
)
@preserve_provenance_text
def candidate_section(
    candidate_id: str, section_name: str, request: Request
) -> dict[str, Any]:
    """Return one latest raw section with fail-closed exact-span DTOs."""
    database = request.app.state.database
    with database.sessions() as session:
        section = session.scalar(
            select(ProfileSection)
            .where(
                ProfileSection.candidate_id == candidate_id,
                ProfileSection.section_name == section_name,
            )
            .order_by(ProfileSection.retrieved_at.desc(), ProfileSection.id.desc())
            .limit(1)
        )
        if section is None:
            raise HTTPException(404, "profile section does not exist")
        redacted, masked_ranges = redact_provenance_text(section.raw_text)
        fields = list(
            session.scalars(
                select(ParsedField)
                .where(ParsedField.profile_section_id == section.id)
                .order_by(ParsedField.span_start, ParsedField.id)
            )
        )
        spans: list[dict[str, Any]] = []
        for field in fields:
            valid = (
                field.candidate_id == candidate_id
                and field.section_name == section_name
                and field.profile_section_id == section.id
                and 0 <= field.span_start < field.span_end <= len(section.raw_text)
                and section.raw_text[field.span_start : field.span_end] == field.snippet
            )
            withheld = not valid or _overlaps(
                field.span_start, field.span_end, masked_ranges
            )
            if withheld:
                spans.append(
                    {
                        "id": field.id,
                        "field_key": field.field_key,
                        "profile_section_id": section.id,
                        "span_start": None,
                        "span_end": None,
                        "value": None,
                        "snippet": None,
                        "verbatim": None,
                        "provenance_available": False,
                        "provenance_label": "Provenance withheld",
                    }
                )
                continue
            verbatim = redacted[field.span_start : field.span_end]
            spans.append(
                {
                    "id": field.id,
                    "field_key": field.field_key,
                    "profile_section_id": section.id,
                    "span_start": field.span_start,
                    "span_end": field.span_end,
                    "value": verbatim,
                    "snippet": verbatim,
                    "verbatim": verbatim,
                    "provenance_available": True,
                    "provenance_label": "Exact stored text",
                }
            )
        return {
            "candidate_id": candidate_id,
            "section_name": section_name,
            "profile_section_id": section.id,
            "raw_text": redacted,
            "span_unit": "unicode_code_point",
            "spans": spans,
        }
