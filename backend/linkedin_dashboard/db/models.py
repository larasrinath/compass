from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_id() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class DashboardSession(Base):
    __tablename__ = "session"
    __table_args__ = (
        CheckConstraint(
            "send_enabled IN (0, 1)", name="ck_session_send_enabled_boolean"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    purge_after: Mapped[str] = mapped_column(String(32), nullable=False)
    nav_budget: Mapped[int] = mapped_column(Integer, nullable=False, default=120)
    nav_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    send_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class PhaseGate(Base):
    __tablename__ = "phase_gate"
    __table_args__ = (
        CheckConstraint("gate IN ('A','B','C')", name="ck_phase_gate_name"),
        UniqueConstraint("session_id", "gate", name="uq_phase_gate_session_gate"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("session.id", ondelete="CASCADE"), nullable=False
    )
    gate: Mapped[str] = mapped_column(String(1), nullable=False)
    accepted_at: Mapped[str] = mapped_column(String(32), nullable=False)
    accepted_note: Mapped[str] = mapped_column(Text, nullable=False)


class RoleBrief(Base):
    __tablename__ = "role_brief"
    __table_args__ = (
        UniqueConstraint("session_id", "version", name="uq_role_brief_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("session.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    superseded_at: Mapped[str | None] = mapped_column(String(32))
    job_description: Mapped[str] = mapped_column(Text, nullable=False)
    target_titles: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    location: Mapped[str] = mapped_column(Text, nullable=False)
    industries: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    positive_keywords: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    negative_keywords: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    message_tone: Mapped[str] = mapped_column(Text, nullable=False)
    weights_version: Mapped[str] = mapped_column(String(64), nullable=False)


class BriefSkill(Base):
    __tablename__ = "brief_skill"
    __table_args__ = (
        CheckConstraint("kind IN ('required','optional')", name="ck_brief_skill_kind"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    brief_id: Mapped[str] = mapped_column(
        ForeignKey("role_brief.id", ondelete="CASCADE"), nullable=False
    )
    term: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    aliases: Mapped[list[str]] = mapped_column(JSON, nullable=False)


class SearchRun(Base):
    __tablename__ = "search_run"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ok','partial','rate_limited','failed')",
            name="ck_search_run_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("session.id", ondelete="CASCADE"), nullable=False
    )
    brief_id: Mapped[str] = mapped_column(
        ForeignKey("role_brief.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    keywords: Mapped[str] = mapped_column(Text, nullable=False)
    location: Mapped[str | None] = mapped_column(Text)
    network: Mapped[list[str] | None] = mapped_column(JSON)
    current_company: Mapped[str | None] = mapped_column(Text)
    result_url: Mapped[str | None] = mapped_column(Text)
    raw_response: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    reference_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    person_reference_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)


class CandidateReference(Base):
    __tablename__ = "candidate_ref"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    search_run_id: Mapped[str] = mapped_column(
        ForeignKey("search_run.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str | None] = mapped_column(Text)
    context: Mapped[str | None] = mapped_column(Text)
    value: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, nullable=False)


class Candidate(Base):
    __tablename__ = "candidate"
    __table_args__ = (
        CheckConstraint(
            "stage IN ('discovered','stage1','stage2')", name="ck_candidate_stage"
        ),
        CheckConstraint(
            "retrieval_status IN ('pending','ok','partial','rate_limited','failed')",
            name="ck_candidate_retrieval_status",
        ),
        UniqueConstraint("session_id", "username", name="uq_candidate_username"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("session.id", ondelete="CASCADE"), nullable=False
    )
    username: Mapped[str] = mapped_column(Text, nullable=False)
    profile_url: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text)
    profile_urn: Mapped[str | None] = mapped_column(Text)
    first_seen_at: Mapped[str] = mapped_column(String(32), nullable=False)
    stage: Mapped[str] = mapped_column(String(16), nullable=False)
    retrieval_status: Mapped[str] = mapped_column(String(16), nullable=False)


class CandidateSource(Base):
    __tablename__ = "candidate_source"

    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("candidate.id", ondelete="CASCADE"), primary_key=True
    )
    search_run_id: Mapped[str] = mapped_column(
        ForeignKey("search_run.id", ondelete="CASCADE"), primary_key=True
    )
    candidate_ref_id: Mapped[str] = mapped_column(
        ForeignKey("candidate_ref.id", ondelete="CASCADE"), nullable=False
    )


class Job(Base):
    __tablename__ = "job"
    __table_args__ = (
        CheckConstraint(
            "state IN ('queued','running','done','failed','interrupted','cancelled')",
            name="ck_job_state",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("session.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    queued_at: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[str | None] = mapped_column(String(32))
    finished_at: Mapped[str | None] = mapped_column(String(32))
    error: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)


class ProfileFetch(Base):
    __tablename__ = "profile_fetch"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('ok','partial','rate_limited','error')",
            name="ck_profile_fetch_outcome",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("candidate.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[str] = mapped_column(
        ForeignKey("job.id", ondelete="CASCADE"), nullable=False
    )
    tool: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_sections: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    args: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    started_at: Mapped[str] = mapped_column(String(32), nullable=False)
    finished_at: Mapped[str | None] = mapped_column(String(32))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    raw_response: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    returned_url: Mapped[str | None] = mapped_column(Text)


class ProfileSection(Base):
    __tablename__ = "profile_section"
    __table_args__ = (
        UniqueConstraint(
            "candidate_id", "section_name", "fetch_id", name="uq_profile_section_fetch"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("candidate.id", ondelete="CASCADE"), nullable=False
    )
    fetch_id: Mapped[str] = mapped_column(
        ForeignKey("profile_fetch.id", ondelete="CASCADE"), nullable=False
    )
    section_name: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    retrieved_at: Mapped[str] = mapped_column(String(32), nullable=False)
    char_len: Mapped[int] = mapped_column(Integer, nullable=False)


class SectionError(Base):
    __tablename__ = "section_error"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    candidate_id: Mapped[str | None] = mapped_column(
        ForeignKey("candidate.id", ondelete="CASCADE")
    )
    search_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("search_run.id", ondelete="CASCADE")
    )
    fetch_id: Mapped[str | None] = mapped_column(
        ForeignKey("profile_fetch.id", ondelete="CASCADE")
    )
    section_name: Mapped[str] = mapped_column(String(64), nullable=False)
    error_type: Mapped[str] = mapped_column(String(64), nullable=False)
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class SectionReference(Base):
    __tablename__ = "section_reference"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("candidate.id", ondelete="CASCADE"), nullable=False
    )
    section_name: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str | None] = mapped_column(Text)
    context: Mapped[str | None] = mapped_column(Text)
    value: Mapped[str | None] = mapped_column(Text)


class ParsedField(Base):
    __tablename__ = "parsed_field"
    __table_args__ = (
        CheckConstraint(
            "origin IN ('deterministic','llm_verified','llm_unverified')",
            name="ck_parsed_field_origin",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("candidate.id", ondelete="CASCADE"), nullable=False
    )
    field_key: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    section_name: Mapped[str] = mapped_column(String(64), nullable=False)
    span_start: Mapped[int] = mapped_column(Integer, nullable=False)
    span_end: Mapped[int] = mapped_column(Integer, nullable=False)
    snippet: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[str] = mapped_column(String(32), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)


class CandidateScore(Base):
    __tablename__ = "score"
    __table_args__ = (
        CheckConstraint("stage IN ('provisional','enriched')", name="ck_score_stage"),
        CheckConstraint(
            "confidence_band IN ('low','medium','high')",
            name="ck_score_confidence_band",
        ),
        CheckConstraint("is_current IN (0, 1)", name="ck_score_is_current_boolean"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("candidate.id", ondelete="CASCADE"), nullable=False
    )
    brief_id: Mapped[str] = mapped_column(
        ForeignKey("role_brief.id", ondelete="CASCADE"), nullable=False
    )
    weights_version: Mapped[str] = mapped_column(String(64), nullable=False)
    stage: Mapped[str] = mapped_column(String(16), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    score_lower: Mapped[float] = mapped_column(Float, nullable=False)
    score_upper: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    confidence_band: Mapped[str] = mapped_column(String(16), nullable=False)
    computed_at: Mapped[str] = mapped_column(String(32), nullable=False)
    superseded_at: Mapped[str | None] = mapped_column(String(32))
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ScoreSignal(Base):
    __tablename__ = "score_signal"
    __table_args__ = (
        CheckConstraint(
            "verdict IN ('matched','partial','not_matched','unknown','contradicted')",
            name="ck_score_signal_verdict",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    score_id: Mapped[str] = mapped_column(
        ForeignKey("score.id", ondelete="CASCADE"), nullable=False
    )
    signal_id: Mapped[str] = mapped_column(String(64), nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False)
    verdict: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_subscore: Mapped[float] = mapped_column(Float, nullable=False)
    contribution: Mapped[float] = mapped_column(Float, nullable=False)
    availability: Mapped[float] = mapped_column(Float, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)


class Evidence(Base):
    __tablename__ = "evidence"
    __table_args__ = (
        CheckConstraint(
            "matcher IN ('exact','alias','stem','llm_verified')",
            name="ck_evidence_matcher",
        ),
        CheckConstraint(
            "polarity IN ('supporting','contradicting')",
            name="ck_evidence_polarity",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    score_signal_id: Mapped[str] = mapped_column(
        ForeignKey("score_signal.id", ondelete="CASCADE"), nullable=False
    )
    parsed_field_id: Mapped[str | None] = mapped_column(
        ForeignKey("parsed_field.id", ondelete="SET NULL")
    )
    section_name: Mapped[str] = mapped_column(String(64), nullable=False)
    span_start: Mapped[int] = mapped_column(Integer, nullable=False)
    span_end: Mapped[int] = mapped_column(Integer, nullable=False)
    snippet: Mapped[str] = mapped_column(Text, nullable=False)
    matcher: Mapped[str] = mapped_column(String(32), nullable=False)
    matched_term: Mapped[str] = mapped_column(Text, nullable=False)
    polarity: Mapped[str] = mapped_column(String(16), nullable=False)
    purged_at: Mapped[str | None] = mapped_column(String(32))


class ShortlistDecision(Base):
    __tablename__ = "shortlist_decision"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('shortlist','reject','undecided')",
            name="ck_shortlist_decision",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("candidate.id", ondelete="CASCADE"), nullable=False
    )
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[str] = mapped_column(String(32), nullable=False)


class MessageDraft(Base):
    __tablename__ = "message_draft"
    __table_args__ = (
        CheckConstraint(
            "generator IN ('llm','template','manual')",
            name="ck_message_draft_generator",
        ),
        CheckConstraint(
            "grounding_status IN ('pass','warn','overridden')",
            name="ck_message_draft_grounding",
        ),
        UniqueConstraint("candidate_id", "version", name="uq_message_draft_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("candidate.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    body_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    generator: Mapped[str] = mapped_column(String(16), nullable=False)
    grounding_status: Mapped[str] = mapped_column(String(16), nullable=False)
    grounding_report: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)


class DraftClaim(Base):
    __tablename__ = "draft_claim"
    __table_args__ = (
        CheckConstraint("grounded IN (0, 1)", name="ck_draft_claim_grounded_boolean"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    draft_id: Mapped[str] = mapped_column(
        ForeignKey("message_draft.id", ondelete="CASCADE"), nullable=False
    )
    claim_text: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_id: Mapped[str | None] = mapped_column(
        ForeignKey("evidence.id", ondelete="SET NULL")
    )
    grounded: Mapped[bool] = mapped_column(Boolean, nullable=False)


class SendConfirmation(Base):
    __tablename__ = "send_confirmation"

    token: Mapped[str] = mapped_column(String(128), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("candidate.id", ondelete="CASCADE"), nullable=False
    )
    draft_id: Mapped[str] = mapped_column(
        ForeignKey("message_draft.id", ondelete="CASCADE"), nullable=False
    )
    body_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    expires_at: Mapped[str] = mapped_column(String(32), nullable=False)
    consumed_at: Mapped[str | None] = mapped_column(String(32))


class SendAttempt(Base):
    __tablename__ = "send_attempt"
    __table_args__ = (
        CheckConstraint(
            "state IN ('SENDING','SENT','FAILED_CONCLUSIVE','AMBIGUOUS',"
            "'DRY_RUN_OK','DRY_RUN_FAILED')",
            name="ck_send_attempt_state",
        ),
        CheckConstraint(
            "resolution IN ('unresolved','confirmed_sent','confirmed_not_sent')",
            name="ck_send_attempt_resolution",
        ),
        CheckConstraint(
            "resolution = 'unresolved' OR state = 'AMBIGUOUS'",
            name="ck_send_attempt_resolution_state",
        ),
        CheckConstraint(
            "(state = 'SENDING' AND finished_at IS NULL) OR "
            "(state <> 'SENDING' AND finished_at IS NOT NULL)",
            name="ck_send_attempt_state_timing",
        ),
        CheckConstraint(
            "(resolution = 'unresolved' AND resolved_at IS NULL "
            "AND resolution_note IS NULL) OR "
            "(resolution <> 'unresolved' AND resolved_at IS NOT NULL)",
            name="ck_send_attempt_resolution_metadata",
        ),
        CheckConstraint(
            "confirm_send IN (0, 1)", name="ck_send_attempt_confirm_send_boolean"
        ),
        CheckConstraint(
            "(confirm_send = 1 AND state IN "
            "('SENDING','SENT','FAILED_CONCLUSIVE','AMBIGUOUS')) OR "
            "(confirm_send = 0 AND state IN ('DRY_RUN_OK','DRY_RUN_FAILED'))",
            name="ck_send_attempt_confirm_send_state",
        ),
        CheckConstraint(
            "tool_sent IS NULL OR tool_sent IN (0, 1)",
            name="ck_send_attempt_tool_sent_boolean",
        ),
        CheckConstraint(
            "tool_recipient_selected IS NULL OR tool_recipient_selected IN (0, 1)",
            name="ck_send_attempt_tool_recipient_selected_boolean",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("candidate.id", ondelete="CASCADE"), nullable=False
    )
    draft_id: Mapped[str] = mapped_column(
        ForeignKey("message_draft.id", ondelete="CASCADE"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )
    body_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    confirm_send: Mapped[bool] = mapped_column(Boolean, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    tool_status: Mapped[str | None] = mapped_column(String(64))
    tool_sent: Mapped[bool | None] = mapped_column(Boolean)
    tool_recipient_selected: Mapped[bool | None] = mapped_column(Boolean)
    tool_url: Mapped[str | None] = mapped_column(Text)
    raw_response: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_class: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[str] = mapped_column(String(32), nullable=False)
    finished_at: Mapped[str | None] = mapped_column(String(32))
    resolution: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unresolved"
    )
    resolved_at: Mapped[str | None] = mapped_column(String(32))
    resolution_note: Mapped[str | None] = mapped_column(Text)


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        CheckConstraint("actor IN ('operator','system')", name="ck_audit_log_actor"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("session.id", ondelete="CASCADE"), nullable=False
    )
    at: Mapped[str] = mapped_column(String(32), nullable=False)
    actor: Mapped[str] = mapped_column(String(16), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
