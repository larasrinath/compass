"""Ranked candidate, score detail, and scoring configuration APIs."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from linkedin_dashboard.api._filters import redact_provenance_text
from linkedin_dashboard.db.models import (
    Candidate,
    CandidateScore,
    CandidateSource,
    Evidence,
    ParsedField,
    PhaseGate,
    ProfileSection,
    ScoreClaim,
    ScoreInputSection,
    ScoreSignal,
    SearchRun,
    SignalCoverage,
    SignalMissingSection,
)
from linkedin_dashboard.services.scoring_service import (
    ConfigVersionConflict,
    ScoringService,
    ScoringValidationError,
)

router = APIRouter(tags=["scoring"])

_LABELS = {
    "S-1": "Required skills",
    "S-2": "Optional skills",
    "S-3": "Relevant experience",
    "S-4": "Title similarity",
    "S-5": "Industry relevance",
    "S-6": "Location fit",
    "S-8": "Credential requirement",
}


def get_service(request: Request) -> ScoringService:
    return request.app.state.scoring_service


class WeightsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: str = Field(min_length=1, max_length=64)
    weights: dict[Annotated[str, Field(max_length=8)], float] = Field(
        min_length=7, max_length=7
    )
    metro_region_equivalences: dict[
        Annotated[str, Field(max_length=240)],
        list[Annotated[str, Field(max_length=240)]],
    ] = Field(default_factory=dict, max_length=100)

    @field_validator("weights", mode="before")
    @classmethod
    def reject_boolean_weights(cls, value: Any) -> Any:
        if isinstance(value, dict) and any(
            isinstance(item, bool) for item in value.values()
        ):
            raise ValueError("boolean weights are not allowed")
        return value


def _gate_a(session: Session, session_id: str) -> bool:
    return (
        session.scalar(
            select(PhaseGate.id).where(
                PhaseGate.session_id == session_id, PhaseGate.gate == "A"
            )
        )
        is not None
    )


def _sort_ranked_records(
    records: list[dict[str, Any]],
    mode: Literal["score_desc", "confidence_desc", "name_asc"],
) -> list[dict[str, Any]]:
    if mode == "confidence_desc":
        key = lambda row: (  # noqa: E731 - local typed sort policy
            row["score"] is None,
            -row["confidence"],
            -(row["score"] or 0),
            row["id"],
        )
    elif mode == "name_asc":
        key = lambda row: (  # noqa: E731 - local typed sort policy
            row["display_name"] is None,
            (row["display_name"] or row["username"]).casefold(),
            row["id"],
        )
    else:
        key = lambda row: (  # noqa: E731 - local typed sort policy
            row["score"] is None,
            -(row["score"] or 0),
            -row["confidence"],
            row["id"],
        )
    return sorted(records, key=key)


def _headline(session: Session, candidate_id: str, score_id: str) -> str | None:
    latest = session.scalar(
        select(ProfileSection)
        .where(
            ProfileSection.candidate_id == candidate_id,
            ProfileSection.section_name == "main_profile",
        )
        .order_by(ProfileSection.retrieved_at.desc(), ProfileSection.id.desc())
        .limit(1)
    )
    if latest is None:
        return None
    sourced = session.scalar(
        select(ScoreInputSection.score_id).where(
            ScoreInputSection.score_id == score_id,
            ScoreInputSection.profile_section_id == latest.id,
            ScoreInputSection.content_sha256 == latest.content_sha256,
        )
    )
    if sourced is None:
        return None
    field = session.scalar(
        select(ParsedField.snippet)
        .where(
            ParsedField.candidate_id == candidate_id,
            ParsedField.field_key == "headline",
            ParsedField.profile_section_id == latest.id,
        )
        .order_by(ParsedField.created_at.desc(), ParsedField.id.desc())
        .limit(1)
    )
    return field


def _non_scoring_hints(session: Session, candidate: Candidate) -> list[dict[str, str]]:
    runs = list(
        session.execute(
            select(SearchRun.id, SearchRun.network)
            .join(CandidateSource, CandidateSource.search_run_id == SearchRun.id)
            .where(CandidateSource.candidate_id == candidate.id)
            .order_by(SearchRun.created_at, SearchRun.id)
        )
    )
    hints = [
        {
            "kind": "network",
            "label": f"Search {run_id} network filter",
            "value": ", ".join(network or []) or "none",
        }
        for run_id, network in runs
    ]
    if candidate.profile_urn:
        hints.append(
            {
                "kind": "profile_urn",
                "label": "Messageability hint",
                "value": "Compose identifier observed; this is never scored.",
            }
        )
    return hints


def ranked_record(
    session: Session, candidate: Candidate, score: CandidateScore
) -> dict[str, Any]:
    signals = list(
        session.scalars(
            select(ScoreSignal)
            .where(ScoreSignal.score_id == score.id, ScoreSignal.rollup.is_not(None))
            .order_by(ScoreSignal.contribution.desc(), ScoreSignal.signal_id)
        )
    )
    previous = session.scalar(
        select(CandidateScore)
        .where(
            CandidateScore.candidate_id == candidate.id,
            CandidateScore.id != score.id,
        )
        .order_by(CandidateScore.computed_at.desc(), CandidateScore.id.desc())
        .limit(1)
    )
    previous_score = previous.score if previous is not None else None
    delta = (
        score.score - previous_score
        if score.score is not None and previous_score is not None
        else None
    )
    return {
        "id": candidate.id,
        "score_id": score.id,
        "input_fingerprint": score.input_fingerprint,
        "username": candidate.username,
        "profile_url": candidate.profile_url,
        "display_name": candidate.display_name,
        "headline": _headline(session, candidate.id, score.id),
        "stage": score.stage,
        "score": score.score,
        "score_lower": score.score_lower,
        "score_upper": score.score_upper,
        "previous_score": previous_score,
        "delta": delta,
        "confidence": score.confidence,
        "confidence_band": score.confidence_band,
        "calculation_status": score.calculation_status,
        "active_signal_count": score.active_signal_count,
        "all_inert_attested": score.all_inert_attested,
        "weights_version": score.weights_version,
        "top_signals": [
            {
                "signal_id": signal.signal_id,
                "label": _LABELS[signal.signal_id],
                "contribution": signal.contribution,
                "rollup": signal.rollup,
            }
            for signal in signals[:3]
        ],
        "non_scoring_hints": _non_scoring_hints(session, candidate),
    }


def _overlaps(start: int, end: int, ranges: tuple[tuple[int, int], ...]) -> bool:
    return any(start < right and left < end for left, right in ranges)


def score_detail(session: Session, score: CandidateScore) -> list[dict[str, Any]]:
    signals = list(
        session.scalars(
            select(ScoreSignal)
            .where(ScoreSignal.score_id == score.id, ScoreSignal.rollup.is_not(None))
            .order_by(ScoreSignal.signal_id)
        )
    )
    output: list[dict[str, Any]] = []
    for signal in signals:
        claims = list(
            session.scalars(
                select(ScoreClaim)
                .where(ScoreClaim.score_signal_id == signal.id)
                .order_by(ScoreClaim.claim_key, ScoreClaim.id)
            )
        )
        claim_records: list[dict[str, Any]] = []
        for claim in claims:
            evidence_records: list[dict[str, Any]] = []
            if claim.evidence_set_id is not None:
                evidence_rows = list(
                    session.scalars(
                        select(Evidence)
                        .where(Evidence.evidence_set_id == claim.evidence_set_id)
                        .order_by(
                            Evidence.section_name, Evidence.span_start, Evidence.id
                        )
                    )
                )
                for evidence in evidence_rows:
                    section = session.get(ProfileSection, evidence.profile_section_id)
                    availability: dict[str, str]
                    snippet = ""
                    if evidence.purged_at is not None or section is None:
                        availability = {
                            "state": "raw_purged",
                            "reason": "Raw profile text was purged.",
                            "purged_at": evidence.purged_at or "unknown",
                        }
                    else:
                        _, masked = redact_provenance_text(section.raw_text)
                        if _overlaps(evidence.span_start, evidence.span_end, masked):
                            availability = {
                                "state": "masked",
                                "reason": (
                                    "This exact span contains masked private data."
                                ),
                            }
                        elif (
                            0
                            <= evidence.span_start
                            < evidence.span_end
                            <= len(section.raw_text)
                            and section.raw_text[
                                evidence.span_start : evidence.span_end
                            ]
                            == evidence.snippet
                        ):
                            availability = {"state": "available"}
                            snippet = evidence.snippet
                        else:
                            availability = {
                                "state": "masked",
                                "reason": "Exact provenance could not be revalidated.",
                            }
                    evidence_records.append(
                        {
                            "id": evidence.id,
                            "section_name": evidence.section_name,
                            "profile_section_id": evidence.profile_section_id,
                            "span_start": evidence.span_start,
                            "span_end": evidence.span_end,
                            "snippet": snippet,
                            "matched_term": (
                                evidence.matched_term
                                if availability["state"] == "available"
                                else ""
                            ),
                            "matcher": evidence.matcher,
                            "polarity": evidence.polarity,
                            "availability": availability,
                        }
                    )
            coverage_records: list[dict[str, Any]] = []
            if claim.coverage_set_id is not None:
                coverage_rows = list(
                    session.execute(
                        select(SignalCoverage, ProfileSection.section_name)
                        .join(
                            ProfileSection,
                            ProfileSection.id == SignalCoverage.profile_section_id,
                        )
                        .where(SignalCoverage.coverage_set_id == claim.coverage_set_id)
                        .order_by(ProfileSection.section_name)
                    )
                )
                coverage_records = [
                    {
                        "section_name": section_name,
                        "normalized_terms": row.normalized_terms,
                        "aliases": row.aliases,
                        "matcher_version": row.matcher_version,
                    }
                    for row, section_name in coverage_rows
                ]
            missing_records = (
                [
                    {"section_name": row.section_name, "reason": row.reason}
                    for row in session.scalars(
                        select(SignalMissingSection)
                        .where(
                            SignalMissingSection.missing_set_id == claim.missing_set_id
                        )
                        .order_by(SignalMissingSection.section_name)
                    )
                ]
                if claim.missing_set_id is not None
                else []
            )
            claim_records.append(
                {
                    "id": claim.id,
                    "claim_key": claim.claim_key,
                    "display_term": claim.display_term,
                    "verdict": claim.verdict,
                    "evidence": evidence_records,
                    "coverage": coverage_records,
                    "missing_sections": missing_records,
                }
            )
        output.append(
            {
                "id": signal.id,
                "signal_id": signal.signal_id,
                "label": _LABELS[signal.signal_id],
                "rollup": signal.rollup,
                "weight": signal.weight,
                "raw_subscore": signal.raw_subscore,
                "contribution": signal.contribution,
                "availability": signal.availability,
                "claims": claim_records,
            }
        )
    return output


@router.get("/weights", response_model=dict[str, Any])
def get_weights(
    service: Annotated[ScoringService, Depends(get_service)],
) -> dict[str, Any]:
    try:
        return service.config_record()
    except LookupError as error:
        raise HTTPException(404, str(error)) from error


@router.put("/weights/current", response_model=dict[str, Any])
def update_weights(
    payload: WeightsInput,
    service: Annotated[ScoringService, Depends(get_service)],
) -> dict[str, Any]:
    try:
        return service.update_config(**payload.model_dump())
    except ConfigVersionConflict as error:
        raise HTTPException(409, str(error)) from error
    except IntegrityError as error:
        raise HTTPException(409, "scoring configuration changed; reload it") from error
    except (LookupError, ScoringValidationError, ValueError) as error:
        raise HTTPException(422, str(error)) from error


@router.get("/candidates", response_model=list[dict[str, Any]])
def ranked_candidates(
    request: Request,
    session_id: Annotated[str, Query(min_length=1, max_length=36)],
    stage: str | None = None,
    min_score: float | None = None,
    confidence: str | None = None,
    sort: Literal["score_desc", "confidence_desc", "name_asc"] = "score_desc",
) -> list[dict[str, Any]]:
    database = request.app.state.database
    with database.sessions() as session:
        if not _gate_a(session, session_id):
            raise HTTPException(409, "Gate A must be accepted before ranking")
        rows = list(
            session.execute(
                select(Candidate, CandidateScore)
                .join(
                    CandidateScore,
                    (CandidateScore.candidate_id == Candidate.id)
                    & CandidateScore.is_current.is_(True),
                )
                .where(Candidate.session_id == session_id)
            )
        )
        records = [
            ranked_record(session, candidate, score) for candidate, score in rows
        ]
    if stage:
        records = [row for row in records if row["stage"] == stage]
    if min_score is not None:
        records = [
            row
            for row in records
            if row["score"] is not None and row["score"] >= min_score
        ]
    if confidence:
        records = [row for row in records if row["confidence_band"] == confidence]
    return _sort_ranked_records(records, sort)


@router.post("/candidates/{candidate_id}/rescore", response_model=dict[str, Any])
def rescore_candidate(
    candidate_id: str,
    service: Annotated[ScoringService, Depends(get_service)],
) -> dict[str, Any]:
    try:
        service.rescore_candidate(candidate_id)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    with service.database.sessions() as session:
        candidate = session.get(Candidate, candidate_id)
        score = session.scalar(
            select(CandidateScore).where(
                CandidateScore.candidate_id == candidate_id,
                CandidateScore.is_current.is_(True),
            )
        )
        if candidate is None or score is None:
            raise HTTPException(500, "rescore did not produce a current score")
        return ranked_record(session, candidate, score)
