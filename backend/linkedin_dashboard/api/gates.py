"""Append-only Phase Gate A/B endpoints."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from linkedin_dashboard.api._filters import redact_provenance_text
from linkedin_dashboard.db.models import (
    Candidate,
    CandidateScore,
    DashboardSession,
    Evidence,
    EvidenceSetRecord,
    PhaseGate,
    PhaseGateEvidence,
    ProfileSection,
    RoleBrief,
    ScoreClaim,
    ScoreInputSection,
    ScoreSignal,
    ScoringConfig,
    SearchRun,
)
from linkedin_dashboard.db.session import Database
from linkedin_dashboard.services.scoring.signals import active_signal_ids
from linkedin_dashboard.services.scoring_persist import (
    load_kernel_brief,
    required_section_names,
)

router = APIRouter(tags=["phase-gates"])


class GateAInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str = Field(min_length=1, max_length=2_000)


class GateBInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_ids: list[str] = Field(min_length=10, max_length=1_000)
    note: str | None = Field(default=None, max_length=2_000)


def get_database(request: Request) -> Database:
    return request.app.state.database


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _current_session_id(database: Database) -> str:
    with database.sessions() as session:
        value = session.scalar(
            select(DashboardSession.id)
            .order_by(DashboardSession.created_at.desc(), DashboardSession.id.desc())
            .limit(1)
        )
    if value is None:
        raise LookupError("session does not exist")
    return value


def _record(gate: PhaseGate, evidence_ids: list[str]) -> dict[str, Any]:
    return {
        "gate": gate.gate,
        "accepted_at": gate.accepted_at,
        "note": gate.accepted_note or None,
        "evidence_ids": evidence_ids,
    }


@router.post("/session/gates/A", response_model=dict[str, Any], status_code=201)
def accept_gate_a(
    payload: GateAInput,
    database: Annotated[Database, Depends(get_database)],
) -> dict[str, Any]:
    note = payload.note.strip()
    if not note:
        raise HTTPException(422, "Gate A note is required")
    try:
        session_id = _current_session_id(database)
        with database.sessions.begin() as session:
            eligible = session.scalar(
                select(SearchRun.id)
                .where(
                    SearchRun.session_id == session_id,
                    SearchRun.status.in_(("ok", "partial", "rate_limited")),
                    SearchRun.processed_at.is_not(None),
                    SearchRun.raw_response.is_not(None),
                )
                .limit(1)
            )
            if eligible is None:
                raise ValueError(
                    "Gate A requires a completed search with persisted results"
                )
            gate = PhaseGate(
                id=str(uuid4()),
                session_id=session_id,
                gate="A",
                accepted_at=_now(),
                accepted_note=note,
                evidence_manifest=[],
            )
            session.add(gate)
            session.flush()
        return _record(gate, [])
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    except IntegrityError as error:
        raise HTTPException(409, "Gate A is already accepted") from error


def _overlaps(start: int, end: int, ranges: tuple[tuple[int, int], ...]) -> bool:
    return any(start < right and left < end for left, right in ranges)


def _score_uses_latest_inputs(session: Any, score: CandidateScore) -> bool:
    brief = session.get(RoleBrief, score.brief_id)
    config = session.get(ScoringConfig, score.scoring_config_id)
    if (
        brief is None
        or brief.superseded_at is not None
        or config is None
        or config.superseded_at is not None
    ):
        return False
    snapshot = score.source_snapshot.get("profile_snapshot", {})
    expected = snapshot.get("sections", []) if isinstance(snapshot, dict) else []
    if not isinstance(expected, list):
        return False
    active = active_signal_ids(load_kernel_brief(session, brief))
    stored_active = score.source_snapshot.get("active_signal_ids")
    if stored_active != [item.value for item in active]:
        return False
    required_names = set(required_section_names(active))
    snapshot_names = [item.get("name") for item in expected if isinstance(item, dict)]
    if (
        len(snapshot_names) != len(set(snapshot_names))
        or set(snapshot_names) != required_names
    ):
        return False
    sources = {
        (row.profile_section_id, row.content_sha256)
        for row in session.scalars(
            select(ScoreInputSection).where(ScoreInputSection.score_id == score.id)
        )
    }
    expected_sources: set[tuple[str, str]] = set()
    for item in expected:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            return False
        latest = session.scalar(
            select(ProfileSection)
            .where(
                ProfileSection.candidate_id == score.candidate_id,
                ProfileSection.section_name == item["name"],
            )
            .order_by(ProfileSection.retrieved_at.desc(), ProfileSection.id.desc())
            .limit(1)
        )
        if item.get("state") == "missing":
            if latest is not None:
                return False
            continue
        if (
            item.get("state") != "complete"
            or latest is None
            or latest.id != item.get("id")
            or latest.content_sha256 != item.get("content_sha256")
        ):
            return False
        expected_sources.add((latest.id, latest.content_sha256))
    return sources == expected_sources


@router.post("/session/gates/B", response_model=dict[str, Any], status_code=201)
def accept_gate_b(
    payload: GateBInput,
    database: Annotated[Database, Depends(get_database)],
) -> dict[str, Any]:
    evidence_ids = list(dict.fromkeys(payload.evidence_ids))
    if len(evidence_ids) < 10:
        raise HTTPException(422, "Gate B requires 10 distinct evidence ids")
    try:
        session_id = _current_session_id(database)
        with database.sessions.begin() as session:
            if (
                session.scalar(
                    select(PhaseGate.id).where(
                        PhaseGate.session_id == session_id, PhaseGate.gate == "A"
                    )
                )
                is None
            ):
                raise ValueError("Gate A must be accepted before Gate B")
            rows = session.execute(
                select(Evidence, CandidateScore, ProfileSection)
                .join(
                    EvidenceSetRecord, EvidenceSetRecord.id == Evidence.evidence_set_id
                )
                .join(ScoreSignal, ScoreSignal.id == Evidence.score_signal_id)
                .join(
                    ScoreClaim,
                    (ScoreClaim.evidence_set_id == EvidenceSetRecord.id)
                    & (ScoreClaim.score_signal_id == ScoreSignal.id),
                )
                .join(CandidateScore, CandidateScore.id == ScoreSignal.score_id)
                .join(Candidate, Candidate.id == CandidateScore.candidate_id)
                .join(ProfileSection, ProfileSection.id == Evidence.profile_section_id)
                .where(
                    Evidence.id.in_(evidence_ids),
                    Candidate.session_id == session_id,
                    CandidateScore.is_current.is_(True),
                    Evidence.purged_at.is_(None),
                    Evidence.evidence_set_id.is_not(None),
                    ScoreClaim.verdict.in_(("matched", "contradicted")),
                )
            ).all()
            by_id = {
                evidence.id: (evidence, score, section)
                for evidence, score, section in rows
            }
            scores = {score.id: score for _, score, _ in rows}
            if any(
                not _score_uses_latest_inputs(session, score)
                for score in scores.values()
            ):
                raise ValueError(
                    "Gate B evidence is stale against the current brief, "
                    "weights, or profile"
                )
            manifest: list[dict[str, str]] = []
            for evidence_id in evidence_ids:
                found = by_id.get(evidence_id)
                if found is None:
                    raise ValueError(
                        "Gate B evidence is stale, purged, or cross-session"
                    )
                evidence, score, section = found
                digest = hashlib.sha256(section.raw_text.encode("utf-8")).hexdigest()
                _, masked = redact_provenance_text(section.raw_text)
                if (
                    evidence.content_sha256 != digest
                    or section.content_sha256 != digest
                    or not (
                        0
                        <= evidence.span_start
                        < evidence.span_end
                        <= len(section.raw_text)
                    )
                    or section.raw_text[evidence.span_start : evidence.span_end]
                    != evidence.snippet
                    or _overlaps(evidence.span_start, evidence.span_end, masked)
                ):
                    raise ValueError(
                        "Gate B evidence is masked or not an exact stored span"
                    )
                manifest.append(
                    {
                        "evidence_id": evidence.id,
                        "score_id": score.id,
                        "input_fingerprint": score.input_fingerprint,
                    }
                )
            gate = PhaseGate(
                id=str(uuid4()),
                session_id=session_id,
                gate="B",
                accepted_at=_now(),
                accepted_note=(payload.note or "").strip(),
                evidence_manifest=manifest,
            )
            session.add(gate)
            session.flush()
            for item in manifest:
                session.add(
                    PhaseGateEvidence(
                        phase_gate_id=gate.id,
                        evidence_id=item["evidence_id"],
                        score_id=item["score_id"],
                        input_fingerprint=item["input_fingerprint"],
                    )
                )
            session.flush()
        return _record(gate, evidence_ids)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    except IntegrityError as error:
        raise HTTPException(
            409, "Gate B is already accepted or evidence changed"
        ) from error
