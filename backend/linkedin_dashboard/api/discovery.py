from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from linkedin_dashboard.api._filters import preserve_provenance_text
from linkedin_dashboard.db.models import RoleBrief
from linkedin_dashboard.services.brief import (
    BriefService,
    BriefValue,
    ProtectedTermError,
    TermValue,
)
from linkedin_dashboard.services.search import SearchService

router = APIRouter(tags=["discovery"])


def get_brief_service(request: Request) -> BriefService:
    return request.app.state.brief_service


def get_search_service(request: Request) -> SearchService:
    return request.app.state.search_service


class SessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=120)
    nav_budget: int = Field(default=120, ge=1, le=500)


class SessionRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: str
    label: str
    purge_after: str
    nav_budget: int
    nav_used: int
    send_enabled: bool


class BriefTermInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    term: str = Field(max_length=160)
    aliases: list[Annotated[str, Field(max_length=160)]] = Field(
        default_factory=list, max_length=30
    )


class BriefInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1, max_length=36)
    job_description: str = Field(min_length=1, max_length=30_000)
    required_skills: list[BriefTermInput] = Field(default_factory=list, max_length=100)
    optional_skills: list[BriefTermInput] = Field(default_factory=list, max_length=100)
    target_titles: list[BriefTermInput] = Field(default_factory=list, max_length=100)
    location: str = Field(default="", max_length=240)
    industries: list[BriefTermInput] = Field(default_factory=list, max_length=100)
    positive_keywords: list[Annotated[str, Field(max_length=160)]] = Field(
        default_factory=list, max_length=100
    )
    negative_keywords: list[Annotated[str, Field(max_length=160)]] = Field(
        default_factory=list, max_length=100
    )
    message_tone: str = Field(default="Professional and concise", max_length=500)

    def as_value(self) -> BriefValue:
        def terms(values: list[BriefTermInput]) -> tuple[TermValue, ...]:
            return tuple(
                TermValue(value.term, tuple(value.aliases)) for value in values
            )

        return BriefValue(
            job_description=self.job_description,
            required_skills=terms(self.required_skills),
            optional_skills=terms(self.optional_skills),
            target_titles=terms(self.target_titles),
            location=self.location,
            industries=terms(self.industries),
            positive_keywords=tuple(self.positive_keywords),
            negative_keywords=tuple(self.negative_keywords),
            message_tone=self.message_tone,
        )


class BriefRecord(BaseModel):
    id: str
    session_id: str
    version: int
    created_at: str
    superseded_at: str | None
    job_description: str
    required_skills: list[BriefTermInput]
    optional_skills: list[BriefTermInput]
    target_titles: list[BriefTermInput]
    location: str
    industries: list[BriefTermInput]
    positive_keywords: list[str]
    negative_keywords: list[str]
    message_tone: str
    weights_version: str
    stale_scores: int = 0


def _brief_record(
    service: BriefService, row: RoleBrief, *, stale_scores: int = 0
) -> BriefRecord:
    value = service.load_value(row.id)
    convert = lambda values: [  # noqa: E731 - compact DTO mapping
        BriefTermInput(term=item.term, aliases=list(item.aliases)) for item in values
    ]
    return BriefRecord(
        id=row.id,
        session_id=row.session_id,
        version=row.version,
        created_at=row.created_at,
        superseded_at=row.superseded_at,
        job_description=value.job_description,
        required_skills=convert(value.required_skills),
        optional_skills=convert(value.optional_skills),
        target_titles=convert(value.target_titles),
        location=value.location,
        industries=convert(value.industries),
        positive_keywords=list(value.positive_keywords),
        negative_keywords=list(value.negative_keywords),
        message_tone=value.message_tone,
        weights_version=row.weights_version,
        stale_scores=stale_scores,
    )


@router.post("/session", response_model=SessionRecord, status_code=201)
def create_session(
    payload: SessionCreate,
    service: Annotated[BriefService, Depends(get_brief_service)],
) -> SessionRecord:
    try:
        return SessionRecord.model_validate(
            service.create_session(payload.label, payload.nav_budget)
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.get("/session", response_model=SessionRecord | None)
def current_session(
    service: Annotated[BriefService, Depends(get_brief_service)],
) -> SessionRecord | None:
    row = service.current_session()
    return SessionRecord.model_validate(row) if row else None


def _save_brief(service: BriefService, payload: BriefInput) -> BriefRecord:
    try:
        row, stale = service.save(payload.session_id, payload.as_value())
    except ProtectedTermError as error:
        raise HTTPException(
            422,
            {
                "message": str(error),
                "offending_terms": error.terms,
            },
        ) from error
    except (LookupError, ValueError) as error:
        raise HTTPException(422, str(error)) from error
    return _brief_record(service, row, stale_scores=stale)


@router.post("/briefs", response_model=BriefRecord, status_code=201)
def create_brief(
    payload: BriefInput,
    service: Annotated[BriefService, Depends(get_brief_service)],
) -> BriefRecord:
    if service.current(payload.session_id) is not None:
        raise HTTPException(409, "A brief already exists; update the current version")
    return _save_brief(service, payload)


@router.put("/briefs/current", response_model=BriefRecord)
def update_brief(
    payload: BriefInput,
    service: Annotated[BriefService, Depends(get_brief_service)],
) -> BriefRecord:
    if service.current(payload.session_id) is None:
        raise HTTPException(404, "No saved brief exists")
    return _save_brief(service, payload)


@router.get("/briefs/current", response_model=BriefRecord | None)
def current_brief(
    session_id: Annotated[str, Query(min_length=1, max_length=36)],
    service: Annotated[BriefService, Depends(get_brief_service)],
) -> BriefRecord | None:
    row = service.current(session_id)
    return _brief_record(service, row) if row else None


class SearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1, max_length=36)
    brief_id: str = Field(min_length=1, max_length=36)
    keywords: str = Field(min_length=1, max_length=500)
    location: str | None = Field(default=None, max_length=200)
    network: list[str] | None = Field(default=None, max_length=3)
    current_company: str | None = Field(default=None, max_length=32)


class SearchQueued(BaseModel):
    job_id: str
    search_run_id: str


async def _enqueue_search(service: SearchService, payload: SearchInput) -> SearchQueued:
    try:
        job_id, run_id = await service.enqueue_search(**payload.model_dump())
    except LookupError as error:
        raise HTTPException(409, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return SearchQueued(job_id=job_id, search_run_id=run_id)


@router.post("/searches", response_model=SearchQueued, status_code=202)
async def enqueue_search(
    payload: SearchInput,
    service: Annotated[SearchService, Depends(get_search_service)],
) -> SearchQueued:
    return await _enqueue_search(service, payload)


@router.get("/searches", response_model=list[dict[str, Any]])
def list_searches(
    session_id: Annotated[str, Query(min_length=1, max_length=36)],
    service: Annotated[SearchService, Depends(get_search_service)],
) -> list[dict[str, Any]]:
    return service.list_runs(session_id)


@router.get("/searches/{run_id}", response_model=dict[str, Any])
@preserve_provenance_text
def search_detail(
    run_id: str,
    service: Annotated[SearchService, Depends(get_search_service)],
) -> dict[str, Any]:
    try:
        return service.get_run(run_id)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error


@router.get("/candidates", response_model=list[dict[str, Any]])
@preserve_provenance_text
def candidates(
    session_id: Annotated[str, Query(min_length=1, max_length=36)],
    service: Annotated[SearchService, Depends(get_search_service)],
) -> list[dict[str, Any]]:
    return service.list_candidates(session_id)


class CompanyLookupInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1, max_length=36)
    slug: str = Field(min_length=1, max_length=200)


class CompanyLookupQueued(BaseModel):
    job_id: str
    lookup_id: str


@router.post(
    "/companies/urn-lookup",
    response_model=CompanyLookupQueued,
    status_code=202,
)
async def company_lookup(
    payload: CompanyLookupInput,
    service: Annotated[SearchService, Depends(get_search_service)],
) -> CompanyLookupQueued:
    try:
        job_id, lookup_id = await service.enqueue_company_lookup(**payload.model_dump())
    except (LookupError, ValueError) as error:
        raise HTTPException(422, str(error)) from error
    return CompanyLookupQueued(job_id=job_id, lookup_id=lookup_id)


@router.get("/companies/urn-lookups/{lookup_id}", response_model=dict[str, Any])
def company_lookup_result(
    lookup_id: str,
    service: Annotated[SearchService, Depends(get_search_service)],
) -> dict[str, Any]:
    try:
        return service.company_lookup(lookup_id)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
