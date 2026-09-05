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


class CandidateIdentityMetadata(Base):
    __tablename__ = "candidate_identity_metadata"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_candidate_identity_metadata_singleton"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    unicode_version: Mapped[str] = mapped_column(String(16), nullable=False)


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
    evidence_manifest: Mapped[list[dict[str, str]]] = mapped_column(
        JSON, nullable=False, server_default="[]"
    )


class PhaseGateEvidence(Base):
    __tablename__ = "phase_gate_evidence"

    phase_gate_id: Mapped[str] = mapped_column(
        ForeignKey("phase_gate.id", ondelete="CASCADE"), primary_key=True
    )
    evidence_id: Mapped[str] = mapped_column(
        ForeignKey("evidence.id", ondelete="RESTRICT"), primary_key=True
    )
    score_id: Mapped[str] = mapped_column(
        ForeignKey("score.id", ondelete="RESTRICT"), nullable=False
    )
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)


class RoleBrief(Base):
    __tablename__ = "role_brief"
    __table_args__ = (
        UniqueConstraint("session_id", "version", name="uq_role_brief_version"),
        CheckConstraint(
            "required_experience_months IS NULL OR required_experience_months >= 0",
            name="ck_role_brief_experience_months",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("session.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    sealed_at: Mapped[str | None] = mapped_column(String(32))
    superseded_at: Mapped[str | None] = mapped_column(String(32))
    job_description: Mapped[str] = mapped_column(Text, nullable=False)
    target_titles: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    location: Mapped[str] = mapped_column(Text, nullable=False)
    industries: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    positive_keywords: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    negative_keywords: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    message_tone: Mapped[str] = mapped_column(Text, nullable=False)
    required_experience_months: Mapped[int | None] = mapped_column(Integer)
    weights_version: Mapped[str] = mapped_column(String(64), nullable=False)
    scoring_inputs: Mapped[dict[str, Any] | None] = mapped_column(JSON)


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
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class BriefTerm(Base):
    """Alias-aware structured brief terms not represented by ``BriefSkill``."""

    __tablename__ = "brief_term"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('target_title','industry')", name="ck_brief_term_kind"
        ),
        UniqueConstraint("brief_id", "kind", "term_key", name="uq_brief_term_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    brief_id: Mapped[str] = mapped_column(
        ForeignKey("role_brief.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    term: Mapped[str] = mapped_column(Text, nullable=False)
    term_key: Mapped[str] = mapped_column(Text, nullable=False)
    aliases: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)


class BriefCredential(Base):
    __tablename__ = "brief_credential"
    __table_args__ = (
        UniqueConstraint("brief_id", "term_key", name="uq_brief_credential_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    brief_id: Mapped[str] = mapped_column(
        ForeignKey("role_brief.id", ondelete="CASCADE"), nullable=False
    )
    term: Mapped[str] = mapped_column(Text, nullable=False)
    term_key: Mapped[str] = mapped_column(Text, nullable=False)
    aliases: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)


class ScoringConfig(Base):
    __tablename__ = "scoring_config"
    __table_args__ = (
        UniqueConstraint("session_id", "version", name="uq_scoring_config_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("session.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    weights: Mapped[dict[str, float]] = mapped_column(JSON, nullable=False)
    metro_region_equivalences: Mapped[dict[str, list[str]]] = mapped_column(
        JSON, nullable=False
    )
    superseded_at: Mapped[str | None] = mapped_column(String(32))


class SearchRun(Base):
    __tablename__ = "search_run"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','ok','partial','rate_limited','failed',"
            "'interrupted','cancelled')",
            name="ck_search_run_status",
        ),
        UniqueConstraint("job_id", name="uq_search_run_job"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("session.id", ondelete="CASCADE"), nullable=False
    )
    brief_id: Mapped[str] = mapped_column(
        ForeignKey("role_brief.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[str] = mapped_column(
        ForeignKey("job.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    keywords: Mapped[str] = mapped_column(Text, nullable=False)
    location: Mapped[str | None] = mapped_column(Text)
    network: Mapped[list[str] | None] = mapped_column(JSON)
    current_company: Mapped[str | None] = mapped_column(Text)
    result_url: Mapped[str | None] = mapped_column(Text)
    raw_response: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    processed_at: Mapped[str | None] = mapped_column(String(32))
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
    # Value-only references use an empty string here; their exact missing-url
    # shape remains in SearchRun.raw_response.
    url: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str | None] = mapped_column(Text)
    context: Mapped[str | None] = mapped_column(Text)
    value: Mapped[str | None] = mapped_column(Text)
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
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
        CheckConstraint(
            "profile_urn_quarantined IN (0, 1)",
            name="ck_candidate_profile_urn_quarantined",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("session.id", ondelete="CASCADE"), nullable=False
    )
    username: Mapped[str] = mapped_column(Text, nullable=False)
    profile_url: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text)
    profile_urn: Mapped[str | None] = mapped_column(Text)
    profile_urn_quarantined: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    profile_contract_error: Mapped[str | None] = mapped_column(Text)
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


class CompanyLookup(Base):
    __tablename__ = "company_lookup"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','ok','not_exposed','failed','interrupted',"
            "'cancelled')",
            name="ck_company_lookup_status",
        ),
        UniqueConstraint("job_id", name="uq_company_lookup_job"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("session.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[str] = mapped_column(
        ForeignKey("job.id", ondelete="CASCADE"), nullable=False
    )
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    raw_response: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    processed_at: Mapped[str | None] = mapped_column(String(32))


class Job(Base):
    __tablename__ = "job"
    __table_args__ = (
        CheckConstraint(
            "state IN "
            "('pending','queued','running','done','failed','interrupted','cancelled')",
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
    claim_token: Mapped[str | None] = mapped_column(String(36))


class JobAttempt(Base):
    __tablename__ = "job_attempt"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('running','ok','error','interrupted')",
            name="ck_job_attempt_outcome",
        ),
        CheckConstraint("attempt_number >= 1", name="ck_job_attempt_number_positive"),
        CheckConstraint(
            "error_class IS NULL OR error_class IN "
            "('AUTH_REQUIRED','BROWSER_BUSY','BROWSER_SETUP','RATE_LIMIT',"
            "'INVALID_REFERENCE','PROFILE_NOT_FOUND','TIMEOUT','TRANSPORT','UNKNOWN')",
            name="ck_job_attempt_error_class",
        ),
        CheckConstraint(
            "outcome <> 'ok' OR (raw_response IS NOT NULL AND raw_response <> 'null')",
            name="ck_job_attempt_ok_has_response",
        ),
        UniqueConstraint("job_id", "attempt_number", name="uq_job_attempt_number"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("job.id", ondelete="CASCADE"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    worker_token: Mapped[str] = mapped_column(String(36), nullable=False)
    started_at: Mapped[str] = mapped_column(String(32), nullable=False)
    response_received_at: Mapped[str | None] = mapped_column(String(32))
    external_call_started_at: Mapped[str | None] = mapped_column(String(32))
    finished_at: Mapped[str | None] = mapped_column(String(32))
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    raw_response: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    raw_error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_class: Mapped[str | None] = mapped_column(String(32))
    safe_error_message: Mapped[str | None] = mapped_column(Text)
    retry_at: Mapped[str | None] = mapped_column(String(32))


class QueueControl(Base):
    __tablename__ = "queue_control"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_queue_control_singleton"),
        CheckConstraint("state IN ('active','paused')", name="ck_queue_control_state"),
        CheckConstraint(
            "rate_limit_count BETWEEN 0 AND 3",
            name="ck_queue_control_rate_limit_count",
        ),
        CheckConstraint(
            "(state = 'active' AND pause_reason IS NULL AND resume_at IS NULL "
            "AND operator_resume_required = 0) OR "
            "(state = 'paused' AND pause_reason IS NOT NULL)",
            name="ck_queue_control_pause_consistency",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    pause_reason: Mapped[str | None] = mapped_column(String(32))
    resume_at: Mapped[str | None] = mapped_column(String(32))
    rate_limit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    operator_resume_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    last_mcp_finished_at: Mapped[str | None] = mapped_column(String(32))
    owner_token: Mapped[str | None] = mapped_column(String(36))
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)


class NavigationReservation(Base):
    __tablename__ = "navigation_reservation"
    __table_args__ = (
        CheckConstraint("cost > 0", name="ck_navigation_reservation_cost"),
        CheckConstraint(
            "refunded_navigations BETWEEN 0 AND cost",
            name="ck_navigation_reservation_refund",
        ),
        CheckConstraint(
            "state IN ('reserved','charged','released')",
            name="ck_navigation_reservation_state",
        ),
    )

    job_id: Mapped[str] = mapped_column(
        ForeignKey("job.id", ondelete="CASCADE"), primary_key=True
    )
    session_id: Mapped[str] = mapped_column(
        ForeignKey("session.id", ondelete="CASCADE"), nullable=False
    )
    cost: Mapped[int] = mapped_column(Integer, nullable=False)
    refunded_navigations: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    reserved_at: Mapped[str] = mapped_column(String(32), nullable=False)
    charged_at: Mapped[str | None] = mapped_column(String(32))
    released_at: Mapped[str | None] = mapped_column(String(32))


class ProfileFetch(Base):
    __tablename__ = "profile_fetch"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_profile_fetch_job"),
        CheckConstraint(
            "outcome IS NULL OR outcome IN ('ok','partial','rate_limited','error')",
            name="ck_profile_fetch_outcome",
        ),
        CheckConstraint(
            "request_stage IN ('stage1','stage2','resume')",
            name="ck_profile_fetch_request_stage",
        ),
        CheckConstraint(
            "(request_stage = 'resume' AND parent_fetch_id IS NOT NULL) OR "
            "(request_stage IN ('stage1','stage2') AND parent_fetch_id IS NULL)",
            name="ck_profile_fetch_parent_stage",
        ),
        CheckConstraint(
            "(projection_payload IS NULL AND projection_source IS NULL) OR "
            "(raw_response IS NOT NULL AND raw_response <> 'null')",
            name="ck_profile_fetch_projection_requires_raw",
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
    outcome: Mapped[str | None] = mapped_column(String(16))
    raw_response: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    projection_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    projection_source: Mapped[str | None] = mapped_column(String(32))
    contract_error: Mapped[str | None] = mapped_column(Text)
    returned_url: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[str | None] = mapped_column(String(32))
    request_stage: Mapped[str] = mapped_column(String(16), nullable=False)
    parent_fetch_id: Mapped[str | None] = mapped_column(
        ForeignKey("profile_fetch.id", ondelete="CASCADE")
    )
    root_fetch_id: Mapped[str] = mapped_column(
        ForeignKey("profile_fetch.id", ondelete="CASCADE"), nullable=False
    )


class ProfileIdentityObservation(Base):
    __tablename__ = "profile_identity_observation"
    __table_args__ = (
        CheckConstraint(
            "verdict IN ('accepted','same','missing','conflict','url_mismatch')",
            name="ck_profile_identity_observation_verdict",
        ),
        UniqueConstraint("fetch_id", name="uq_profile_identity_observation_fetch"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("candidate.id", ondelete="CASCADE"), nullable=False
    )
    fetch_id: Mapped[str] = mapped_column(
        ForeignKey("profile_fetch.id", ondelete="CASCADE"), nullable=False
    )
    returned_url: Mapped[str | None] = mapped_column(Text)
    observed_urn: Mapped[str | None] = mapped_column(Text)
    verdict: Mapped[str] = mapped_column(String(32), nullable=False)
    observed_at: Mapped[str] = mapped_column(String(32), nullable=False)


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
    content_sha256: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=""
    )
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
    source_item: Mapped[dict[str, Any] | None] = mapped_column(JSON)


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
    fetch_id: Mapped[str | None] = mapped_column(
        ForeignKey("profile_fetch.id", ondelete="CASCADE")
    )
    source_position: Mapped[int | None] = mapped_column(Integer)


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
    # Added as a nullable upgrade column so an old, manually-populated M2
    # database can still open.  A database trigger requires it for every new
    # field, and the M3 service never constructs a field without this lineage.
    profile_section_id: Mapped[str | None] = mapped_column(
        ForeignKey("profile_section.id", ondelete="CASCADE")
    )


class CandidateScore(Base):
    __tablename__ = "score"
    __table_args__ = (
        CheckConstraint("stage IN ('provisional','enriched')", name="ck_score_stage"),
        CheckConstraint(
            "confidence_band IS NULL OR confidence_band IN ('low','medium','high')",
            name="ck_score_confidence_band",
        ),
        CheckConstraint(
            "calculation_status IN ('scored','unknown')",
            name="ck_score_calculation_status",
        ),
        CheckConstraint("active_signal_count >= 0", name="ck_score_active_count"),
        CheckConstraint(
            "all_inert_attested IN (0, 1)", name="ck_score_all_inert_boolean"
        ),
        CheckConstraint("is_current IN (0, 1)", name="ck_score_is_current_boolean"),
        UniqueConstraint(
            "candidate_id", "input_fingerprint", name="uq_score_candidate_input"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("candidate.id", ondelete="CASCADE"), nullable=False
    )
    brief_id: Mapped[str] = mapped_column(
        ForeignKey("role_brief.id", ondelete="CASCADE"), nullable=False
    )
    weights_version: Mapped[str] = mapped_column(String(64), nullable=False)
    scoring_config_id: Mapped[str | None] = mapped_column(
        ForeignKey("scoring_config.id", ondelete="RESTRICT")
    )
    stage: Mapped[str] = mapped_column(String(16), nullable=False)
    score: Mapped[float | None] = mapped_column(Float)
    score_lower: Mapped[float | None] = mapped_column(Float)
    score_upper: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    confidence_band: Mapped[str | None] = mapped_column(String(16))
    calculation_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="scored"
    )
    active_signal_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
    all_inert_attested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="0"
    )
    input_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="legacy"
    )
    source_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, server_default="{}"
    )
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
    rollup: Mapped[str | None] = mapped_column(String(32))
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
    evidence_set_id: Mapped[str | None] = mapped_column(
        ForeignKey("evidence_set.id", ondelete="CASCADE")
    )
    parsed_field_id: Mapped[str | None] = mapped_column(
        ForeignKey("parsed_field.id", ondelete="SET NULL")
    )
    section_name: Mapped[str] = mapped_column(String(64), nullable=False)
    profile_section_id: Mapped[str | None] = mapped_column(
        ForeignKey("profile_section.id", ondelete="RESTRICT")
    )
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    span_start: Mapped[int] = mapped_column(Integer, nullable=False)
    span_end: Mapped[int] = mapped_column(Integer, nullable=False)
    snippet: Mapped[str] = mapped_column(Text, nullable=False)
    matcher: Mapped[str] = mapped_column(String(32), nullable=False)
    matched_term: Mapped[str] = mapped_column(Text, nullable=False)
    polarity: Mapped[str] = mapped_column(String(16), nullable=False)
    purged_at: Mapped[str | None] = mapped_column(String(32))


class ScoreInputSection(Base):
    __tablename__ = "score_input_section"

    score_id: Mapped[str] = mapped_column(
        ForeignKey("score.id", ondelete="CASCADE"), primary_key=True
    )
    profile_section_id: Mapped[str] = mapped_column(
        ForeignKey("profile_section.id", ondelete="RESTRICT"), primary_key=True
    )
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)


class EvidenceSetRecord(Base):
    __tablename__ = "evidence_set"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("candidate.id", ondelete="CASCADE"), nullable=False
    )
    score_signal_id: Mapped[str] = mapped_column(
        ForeignKey("score_signal.id", ondelete="CASCADE"), nullable=False
    )


class CoverageSetRecord(Base):
    __tablename__ = "coverage_set"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("candidate.id", ondelete="CASCADE"), nullable=False
    )
    score_signal_id: Mapped[str] = mapped_column(
        ForeignKey("score_signal.id", ondelete="CASCADE"), nullable=False
    )
    required_sections: Mapped[list[str]] = mapped_column(JSON, nullable=False)


class SignalCoverage(Base):
    __tablename__ = "signal_coverage"
    __table_args__ = (
        UniqueConstraint(
            "coverage_set_id", "profile_section_id", name="uq_signal_coverage_section"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    coverage_set_id: Mapped[str] = mapped_column(
        ForeignKey("coverage_set.id", ondelete="CASCADE"), nullable=False
    )
    profile_section_id: Mapped[str] = mapped_column(
        ForeignKey("profile_section.id", ondelete="RESTRICT"), nullable=False
    )
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_terms: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    aliases: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    matcher_version: Mapped[str] = mapped_column(String(32), nullable=False)


class MissingSetRecord(Base):
    __tablename__ = "missing_set"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("candidate.id", ondelete="CASCADE"), nullable=False
    )
    score_signal_id: Mapped[str] = mapped_column(
        ForeignKey("score_signal.id", ondelete="CASCADE"), nullable=False
    )


class SignalMissingSection(Base):
    __tablename__ = "signal_missing_section"
    __table_args__ = (
        CheckConstraint(
            "reason IN ('not_requested','rate_limit','fetch_error','unparseable')",
            name="ck_signal_missing_reason",
        ),
        UniqueConstraint(
            "missing_set_id", "section_name", "reason", name="uq_missing_section"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    missing_set_id: Mapped[str] = mapped_column(
        ForeignKey("missing_set.id", ondelete="CASCADE"), nullable=False
    )
    section_name: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    section_error_id: Mapped[str | None] = mapped_column(
        ForeignKey("section_error.id", ondelete="SET NULL")
    )


class ScoreClaim(Base):
    __tablename__ = "score_claim"
    __table_args__ = (
        CheckConstraint(
            "verdict IN ('matched','not_matched','unknown','contradicted')",
            name="ck_score_claim_verdict",
        ),
        CheckConstraint(
            "(evidence_set_id IS NOT NULL) + (coverage_set_id IS NOT NULL) + "
            "(missing_set_id IS NOT NULL) = 1",
            name="ck_score_claim_one_provenance",
        ),
        CheckConstraint(
            "((verdict IN ('matched','contradicted') AND evidence_set_id IS NOT NULL) "
            "OR (verdict='not_matched' AND coverage_set_id IS NOT NULL) "
            "OR (verdict='unknown' AND missing_set_id IS NOT NULL))",
            name="ck_score_claim_compatible_provenance",
        ),
        UniqueConstraint("score_signal_id", "claim_key", name="uq_score_claim_key"),
        UniqueConstraint("evidence_set_id", name="uq_score_claim_evidence_set"),
        UniqueConstraint("coverage_set_id", name="uq_score_claim_coverage_set"),
        UniqueConstraint("missing_set_id", name="uq_score_claim_missing_set"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    score_signal_id: Mapped[str] = mapped_column(
        ForeignKey("score_signal.id", ondelete="CASCADE"), nullable=False
    )
    claim_key: Mapped[str] = mapped_column(Text, nullable=False)
    display_term: Mapped[str] = mapped_column(Text, nullable=False)
    verdict: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_set_id: Mapped[str | None] = mapped_column(
        ForeignKey("evidence_set.id", ondelete="RESTRICT")
    )
    coverage_set_id: Mapped[str | None] = mapped_column(
        ForeignKey("coverage_set.id", ondelete="RESTRICT")
    )
    missing_set_id: Mapped[str | None] = mapped_column(
        ForeignKey("missing_set.id", ondelete="RESTRICT")
    )


class ScorePenalty(Base):
    __tablename__ = "score_penalty"
    __table_args__ = (
        CheckConstraint("penalty_id IN ('P-1','P-2')", name="ck_score_penalty_id"),
        UniqueConstraint("score_id", "penalty_id", name="uq_score_penalty_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    score_id: Mapped[str] = mapped_column(
        ForeignKey("score.id", ondelete="CASCADE"), nullable=False
    )
    penalty_id: Mapped[str] = mapped_column(String(3), nullable=False)
    points: Mapped[float] = mapped_column(Float, nullable=False)
    details: Mapped[list[str]] = mapped_column(JSON, nullable=False)


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
