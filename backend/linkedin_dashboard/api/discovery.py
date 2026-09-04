from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr
from sqlalchemy import select

from linkedin_dashboard.api._filters import (
    preserve_brief_domain_credentials,
    preserve_provenance_text,
)
from linkedin_dashboard.db.models import PhaseGate, PhaseGateEvidence, RoleBrief
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

    label: StrictStr = Field(min_length=1, max_length=120)
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
    phase_gates: dict[str, Any] = Field(default_factory=dict)


class BriefTermInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    term: StrictStr = Field(max_length=160)
    aliases: list[Annotated[StrictStr, Field(max_length=160)]] = Field(
        default_factory=list, max_length=30
    )


class BriefInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: StrictStr = Field(min_length=1, max_length=36)
    job_description: StrictStr = Field(min_length=1, max_length=30_000)
    required_skills: list[BriefTermInput] = Field(default_factory=list, max_length=100)
    optional_skills: list[BriefTermInput] = Field(default_factory=list, max_length=100)
    target_titles: list[BriefTermInput] = Field(default_factory=list, max_length=100)
    location: StrictStr = Field(default="", max_length=240)
    industries: list[BriefTermInput] = Field(default_factory=list, max_length=100)
    positive_keywords: list[Annotated[StrictStr, Field(max_length=160)]] = Field(
        default_factory=list, max_length=100
    )
    negative_keywords: list[Annotated[StrictStr, Field(max_length=160)]] = Field(
        default_factory=list, max_length=100
    )
    message_tone: StrictStr = Field(default="Professional and concise", max_length=500)
    required_experience_months: StrictInt | None = Field(default=None, ge=0)
    required_credentials: list[BriefTermInput] | None = Field(
        default=None, max_length=100
    )

    def as_value(self, previous: BriefValue | None = None) -> BriefValue:
        def terms(values: list[BriefTermInput] | None) -> tuple[TermValue, ...]:
            return tuple(
                TermValue(value.term, tuple(value.aliases)) for value in (values or [])
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
            required_experience_months=(
                self.required_experience_months
                if "required_experience_months" in self.model_fields_set
                or previous is None
                else previous.required_experience_months
            ),
            required_credentials=(
                terms(self.required_credentials)
                if "required_credentials" in self.model_fields_set or previous is None
                else previous.required_credentials
            ),
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
    required_experience_months: int | None
    required_credentials: list[BriefTermInput]
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
        required_experience_months=value.required_experience_months,
        required_credentials=convert(value.required_credentials),
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
    if row is None:
        return None
    with service.database.sessions() as session:
        gates = list(
            session.scalars(
                select(PhaseGate)
                .where(PhaseGate.session_id == row.id)
                .order_by(PhaseGate.gate)
            )
        )
        records: dict[str, Any] = {}
        for gate in gates:
            evidence_ids = list(
                session.scalars(
                    select(PhaseGateEvidence.evidence_id)
                    .where(PhaseGateEvidence.phase_gate_id == gate.id)
                    .order_by(PhaseGateEvidence.evidence_id)
                )
            )
            records[gate.gate] = {
                "gate": gate.gate,
                "accepted_at": gate.accepted_at,
                "note": gate.accepted_note or None,
                "evidence_ids": evidence_ids,
            }
    return SessionRecord.model_validate(row).model_copy(update={"phase_gates": records})


def _save_brief(
    service: BriefService, payload: BriefInput, *, preserve_omitted: bool = False
) -> BriefRecord:
    try:
        current = service.current(payload.session_id)
        previous = (
            service.load_value(current.id)
            if preserve_omitted and current is not None
            else None
        )
        row, stale = service.save(payload.session_id, payload.as_value(previous))
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
@preserve_brief_domain_credentials
def create_brief(
    payload: BriefInput,
    service: Annotated[BriefService, Depends(get_brief_service)],
) -> BriefRecord:
    if service.current(payload.session_id) is not None:
        raise HTTPException(409, "A brief already exists; update the current version")
    return _save_brief(service, payload)


@router.put("/briefs/current", response_model=BriefRecord)
@preserve_brief_domain_credentials
def update_brief(
    payload: BriefInput,
    service: Annotated[BriefService, Depends(get_brief_service)],
) -> BriefRecord:
    if service.current(payload.session_id) is None:
        raise HTTPException(404, "No saved brief exists")
    return _save_brief(service, payload, preserve_omitted=True)


@router.get("/briefs/current", response_model=BriefRecord | None)
@preserve_brief_domain_credentials
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


@router.get("/candidate-pool", response_model=list[dict[str, Any]])
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
