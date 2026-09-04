"""Atomic persistence adapter for the pure M4 scoring kernel."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from linkedin_dashboard.db.models import (
    BriefCredential,
    BriefSkill,
    BriefTerm,
    Candidate,
    CandidateScore,
    CoverageSetRecord,
    Evidence,
    EvidenceSetRecord,
    MissingSetRecord,
    ParsedField,
    RoleBrief,
    ScoreInputSection,
    ScorePenalty,
    ScoringConfig,
    SectionError,
    SignalCoverage,
    SignalMissingSection,
)
from linkedin_dashboard.db.models import (
    ProfileSection as ProfileSectionRow,
)
from linkedin_dashboard.db.models import (
    ScoreClaim as ScoreClaimRow,
)
from linkedin_dashboard.db.models import (
    ScoreSignal as ScoreSignalRow,
)
from linkedin_dashboard.parsing.verify import verify_substring
from linkedin_dashboard.services.scoring.aggregate import calculate_score
from linkedin_dashboard.services.scoring.signals import active_signal_ids
from linkedin_dashboard.services.scoring.types import (
    BriefInput,
    CoverageSet,
    EvidenceSet,
    ExperienceRole,
    MetroEquivalence,
    MissingReason,
    MissingSet,
    MonthsDerivation,
    ProfileSection,
    ProfileSnapshot,
    ScoreCalculation,
    ScoreStage,
    ScoringConfigInput,
    SectionState,
    SignalId,
    SignalWeight,
    SourcedText,
    Term,
)

ALGORITHM_VERSION = "m4-v1"
_SIGNAL_SECTIONS: dict[SignalId, tuple[str, ...]] = {
    SignalId.REQUIRED_SKILLS: ("skills", "experience", "main_profile"),
    SignalId.OPTIONAL_SKILLS: ("skills", "experience", "main_profile"),
    SignalId.EXPERIENCE: ("experience",),
    SignalId.TITLE: ("main_profile", "experience"),
    SignalId.INDUSTRY: ("experience", "main_profile"),
    SignalId.LOCATION: ("main_profile",),
    SignalId.CREDENTIAL: ("education", "certifications"),
}
_DURATION = re.compile(
    r"(?:(?P<years>\d+)\s+yrs?)?(?:\s*[·,]?\s*)?(?:(?P<months>\d+)\s+mos?)?",
    re.IGNORECASE,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _terms(rows: list[Any]) -> tuple[Term, ...]:
    return tuple(Term(row.term, tuple(row.aliases)) for row in rows)


def load_kernel_brief(session: Session, brief: RoleBrief) -> BriefInput:
    skills = list(
        session.scalars(
            select(BriefSkill)
            .where(BriefSkill.brief_id == brief.id)
            .order_by(BriefSkill.position, BriefSkill.id)
        )
    )
    terms = list(
        session.scalars(
            select(BriefTerm)
            .where(BriefTerm.brief_id == brief.id)
            .order_by(BriefTerm.position, BriefTerm.id)
        )
    )
    credentials = list(
        session.scalars(
            select(BriefCredential)
            .where(BriefCredential.brief_id == brief.id)
            .order_by(BriefCredential.position, BriefCredential.id)
        )
    )
    return BriefInput(
        required_skills=_terms([row for row in skills if row.kind == "required"]),
        optional_skills=_terms([row for row in skills if row.kind == "optional"]),
        required_experience_months=brief.required_experience_months,
        target_titles=_terms([row for row in terms if row.kind == "target_title"]),
        industries=_terms([row for row in terms if row.kind == "industry"]),
        location=brief.location,
        required_credentials=_terms(credentials),
        positive_keywords=tuple(brief.positive_keywords),
        negative_keywords=tuple(brief.negative_keywords),
    )


def load_kernel_config(config: ScoringConfig) -> ScoringConfigInput:
    return ScoringConfigInput(
        weights=tuple(
            SignalWeight(SignalId(signal_id), Decimal(str(value)))
            for signal_id, value in sorted(config.weights.items())
        ),
        metro_equivalences=tuple(
            MetroEquivalence(name, tuple(locations))
            for name, locations in sorted(config.metro_region_equivalences.items())
        ),
    )


def _latest_sections(
    session: Session, candidate_id: str
) -> dict[str, ProfileSectionRow]:
    rows = list(
        session.scalars(
            select(ProfileSectionRow)
            .where(ProfileSectionRow.candidate_id == candidate_id)
            .order_by(
                ProfileSectionRow.retrieved_at.desc(), ProfileSectionRow.id.desc()
            )
        )
    )
    latest: dict[str, ProfileSectionRow] = {}
    for row in rows:
        latest.setdefault(row.section_name, row)
    return latest


def _missing_reason(
    session: Session, candidate_id: str, section_name: str
) -> tuple[MissingReason, str | None]:
    error = session.scalar(
        select(SectionError)
        .where(
            SectionError.candidate_id == candidate_id,
            SectionError.section_name == section_name,
        )
        .order_by(SectionError.id.desc())
        .limit(1)
    )
    if error is None:
        return MissingReason.NOT_REQUESTED, None
    if error.error_type.casefold() == "rate_limit":
        return MissingReason.RATE_LIMIT, error.id
    if error.error_type.casefold() in {"parse_error", "unparseable"}:
        return MissingReason.UNPARSEABLE, error.id
    return MissingReason.FETCH_ERROR, error.id


def _sourced(field: ParsedField, section: ProfileSectionRow) -> SourcedText | None:
    span = verify_substring(
        section.raw_text, field.snippet, start_hint=field.span_start
    )
    if (
        span is None
        or span.end != field.span_end
        or field.section_name != section.section_name
        or field.candidate_id != section.candidate_id
    ):
        return None
    return SourcedText(
        section_name=section.section_name,
        section_id=section.id,
        content_sha256=section.content_sha256,
        text=field.snippet,
        span=span,
    )


def _duration_months(value: str) -> int | None:
    for match in _DURATION.finditer(value):
        if match.group("years") or match.group("months"):
            return int(match.group("years") or 0) * 12 + int(match.group("months") or 0)
    return None


def build_snapshot(
    session: Session, candidate: Candidate, active: tuple[SignalId, ...]
) -> tuple[ProfileSnapshot, tuple[ProfileSectionRow, ...]]:
    latest = _latest_sections(session, candidate.id)
    required_names = tuple(
        sorted({name for signal_id in active for name in _SIGNAL_SECTIONS[signal_id]})
    )
    sections: list[ProfileSection] = []
    consumed: list[ProfileSectionRow] = []
    for name in required_names:
        row = latest.get(name)
        if row is None:
            reason, error_id = _missing_reason(session, candidate.id, name)
            sections.append(
                ProfileSection(
                    section_id=f"missing:{name}",
                    name=name,
                    state=SectionState.MISSING,
                    missing_reason=reason,
                    section_error_id=error_id,
                )
            )
            continue
        digest = _sha256(row.raw_text)
        if row.content_sha256 != digest:
            raise ValueError("profile section content hash mismatch")
        sections.append(
            ProfileSection(
                section_id=row.id,
                name=name,
                state=SectionState.COMPLETE,
                raw_text=row.raw_text,
                content_sha256=digest,
            )
        )
        consumed.append(row)

    fields = (
        list(
            session.scalars(
                select(ParsedField)
                .where(
                    ParsedField.candidate_id == candidate.id,
                    ParsedField.profile_section_id.in_([row.id for row in consumed]),
                    ParsedField.origin.in_(("deterministic", "llm_verified")),
                )
                .order_by(
                    ParsedField.section_name, ParsedField.span_start, ParsedField.id
                )
            )
        )
        if consumed
        else []
    )
    row_by_id = {row.id: row for row in consumed}
    sourced: list[tuple[ParsedField, SourcedText]] = []
    for field in fields:
        row = row_by_id.get(field.profile_section_id or "")
        value = _sourced(field, row) if row is not None else None
        if value is not None:
            sourced.append((field, value))

    titles = tuple(
        value
        for field, value in sourced
        if field.field_key == "headline" or field.field_key.endswith(".title")
    )
    location = next(
        (value for field, value in sourced if field.field_key == "location"), None
    )
    grouped: dict[str, dict[str, SourcedText]] = {}
    for field, value in sourced:
        match = re.fullmatch(
            r"experience\.(\d+)\.(title|dates|duration|description)", field.field_key
        )
        if match:
            grouped.setdefault(match.group(1), {})[match.group(2)] = value
    roles: list[ExperienceRole] = []
    for key in sorted(grouped, key=int):
        values = grouped[key]
        title = values.get("title")
        if title is None:
            continue
        duration = values.get("duration") or values.get("dates")
        months = _duration_months(duration.text) if duration is not None else None
        roles.append(
            ExperienceRole(
                title=title,
                description=values.get("description"),
                date_range=values.get("dates"),
                duration=duration if months is not None else None,
                months=months,
                months_derivation=(
                    MonthsDerivation.DURATION_TEXT if months is not None else None
                ),
            )
        )
    return (
        ProfileSnapshot(
            sections=tuple(sections),
            titles=titles,
            location=location,
            experience_roles=tuple(roles),
        ),
        tuple(sorted(consumed, key=lambda row: row.section_name)),
    )


def input_fingerprint(
    *,
    candidate: Candidate,
    brief: RoleBrief,
    kernel_brief: BriefInput,
    config: ScoringConfig,
    active: tuple[SignalId, ...],
    snapshot: ProfileSnapshot,
    sections: tuple[ProfileSectionRow, ...],
) -> tuple[str, dict[str, Any]]:
    def sourced(value: SourcedText | None) -> dict[str, Any] | None:
        if value is None:
            return None
        return {
            "section_name": value.section_name,
            "section_id": value.section_id,
            "content_sha256": value.content_sha256,
            "text": value.text,
            "span_start": value.span.start,
            "span_end": value.span.end,
        }

    payload: dict[str, Any] = {
        "algorithm_version": ALGORITHM_VERSION,
        "candidate_id": candidate.id,
        "brief": {
            "id": brief.id,
            "version": brief.version,
            "required_skills": [
                {"term": item.term, "aliases": list(item.aliases)}
                for item in kernel_brief.required_skills
            ],
            "optional_skills": [
                {"term": item.term, "aliases": list(item.aliases)}
                for item in kernel_brief.optional_skills
            ],
            "required_experience_months": kernel_brief.required_experience_months,
            "target_titles": [
                {"term": item.term, "aliases": list(item.aliases)}
                for item in kernel_brief.target_titles
            ],
            "industries": [
                {"term": item.term, "aliases": list(item.aliases)}
                for item in kernel_brief.industries
            ],
            "location": kernel_brief.location,
            "required_credentials": [
                {"term": item.term, "aliases": list(item.aliases)}
                for item in kernel_brief.required_credentials
            ],
        },
        "config": {
            "id": config.id,
            "version": config.version,
            "weights": config.weights,
            "metro_region_equivalences": config.metro_region_equivalences,
        },
        "active_signal_ids": [item.value for item in active],
        "profile_snapshot": {
            "sections": [
                {
                    "id": item.section_id,
                    "name": item.name,
                    "state": item.state.value,
                    "content_sha256": item.content_sha256 or None,
                    "missing_reason": (
                        item.missing_reason.value
                        if item.missing_reason is not None
                        else None
                    ),
                    "section_error_id": item.section_error_id,
                }
                for item in snapshot.sections
            ],
            "titles": [sourced(item) for item in snapshot.titles],
            "location": sourced(snapshot.location),
            "experience_roles": [
                {
                    "title": sourced(role.title),
                    "description": sourced(role.description),
                    "date_range": sourced(role.date_range),
                    "duration": sourced(role.duration),
                    "months": role.months,
                    "months_derivation": (
                        role.months_derivation.value
                        if role.months_derivation is not None
                        else None
                    ),
                }
                for role in snapshot.experience_roles
            ],
        },
        "sections": [
            {"id": row.id, "name": row.section_name, "sha256": row.content_sha256}
            for row in sections
        ],
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return _sha256(canonical), payload


def _float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _persist_claim(
    session: Session,
    *,
    candidate: Candidate,
    signal_row: ScoreSignalRow,
    claim: Any,
) -> None:
    set_id = str(uuid4())
    evidence_set_id = coverage_set_id = missing_set_id = None
    if isinstance(claim.provenance, EvidenceSet):
        evidence_set_id = set_id
        session.add(EvidenceSetRecord(id=set_id, candidate_id=candidate.id))
        session.flush()
        for entry in claim.provenance.entries:
            evidence_id = str(uuid4())
            session.add(
                Evidence(
                    id=evidence_id,
                    score_signal_id=signal_row.id,
                    evidence_set_id=set_id,
                    parsed_field_id=None,
                    section_name=entry.section_name,
                    profile_section_id=str(entry.profile_section_id),
                    content_sha256=entry.content_sha256,
                    span_start=entry.span.start,
                    span_end=entry.span.end,
                    snippet=entry.span.snippet,
                    matcher=entry.matcher.value,
                    matched_term=entry.matched_term,
                    polarity=entry.polarity.value,
                    purged_at=None,
                )
            )
        session.flush()
    elif isinstance(claim.provenance, CoverageSet):
        coverage_set_id = set_id
        required = [entry.section_name for entry in claim.provenance.entries]
        session.add(
            CoverageSetRecord(
                id=set_id, candidate_id=candidate.id, required_sections=required
            )
        )
        session.flush()
        for entry in claim.provenance.entries:
            session.add(
                SignalCoverage(
                    id=str(uuid4()),
                    coverage_set_id=set_id,
                    profile_section_id=str(entry.profile_section_id),
                    content_sha256=entry.content_sha256,
                    normalized_terms=list(entry.normalized_terms),
                    aliases=list(entry.aliases),
                    matcher_version=entry.matcher_version,
                )
            )
        session.flush()
    elif isinstance(claim.provenance, MissingSet):
        missing_set_id = set_id
        session.add(MissingSetRecord(id=set_id, candidate_id=candidate.id))
        session.flush()
        for entry in claim.provenance.entries:
            session.add(
                SignalMissingSection(
                    id=str(uuid4()),
                    missing_set_id=set_id,
                    section_name=entry.section_name,
                    reason=entry.reason.value,
                    section_error_id=(
                        str(entry.section_error_id)
                        if entry.section_error_id is not None
                        else None
                    ),
                )
            )
        session.flush()
    else:  # pragma: no cover - the closed kernel union makes this unreachable
        raise TypeError("unknown claim provenance")
    session.add(
        ScoreClaimRow(
            id=str(uuid4()),
            score_signal_id=signal_row.id,
            claim_key=claim.claim_key,
            display_term=claim.display_term,
            verdict=claim.verdict.value,
            evidence_set_id=evidence_set_id,
            coverage_set_id=coverage_set_id,
            missing_set_id=missing_set_id,
        )
    )
    session.flush()


def persist_calculation(
    session: Session,
    *,
    candidate: Candidate,
    brief: RoleBrief,
    config: ScoringConfig,
    calculation: ScoreCalculation,
    fingerprint: str,
    fingerprint_payload: dict[str, Any],
    source_sections: tuple[ProfileSectionRow, ...],
) -> CandidateScore:
    now = _now()
    score_row = CandidateScore(
        id=str(uuid4()),
        candidate_id=candidate.id,
        brief_id=brief.id,
        weights_version=str(config.version),
        scoring_config_id=config.id,
        stage=calculation.stage.value,
        score=_float(calculation.score),
        score_lower=_float(calculation.score_lower),
        score_upper=_float(calculation.score_upper),
        confidence=float(calculation.confidence),
        confidence_band=(
            calculation.confidence_band.value
            if calculation.confidence_band is not None
            else None
        ),
        calculation_status=calculation.calculation_status.value,
        active_signal_count=calculation.active_signal_count,
        all_inert_attested=calculation.active_signal_count == 0,
        input_fingerprint=fingerprint,
        source_snapshot=fingerprint_payload,
        computed_at=now,
        superseded_at=None,
        is_current=False,
    )
    session.add(score_row)
    session.flush()
    for section in source_sections:
        session.add(
            ScoreInputSection(
                score_id=score_row.id,
                profile_section_id=section.id,
                content_sha256=section.content_sha256,
            )
        )
    for signal in calculation.signals:
        weight = Decimal(str(config.weights[signal.signal_id.value]))
        signal_row = ScoreSignalRow(
            id=str(uuid4()),
            score_id=score_row.id,
            signal_id=signal.signal_id.value,
            weight=float(weight),
            verdict=(
                "partial" if signal.rollup.value == "mixed" else signal.rollup.value
            ),
            rollup=signal.rollup.value,
            raw_subscore=float(signal.raw_subscore),
            contribution=float(weight * signal.availability * signal.raw_subscore),
            availability=float(signal.availability),
            note=None,
        )
        session.add(signal_row)
        session.flush()
        for claim in signal.claims:
            _persist_claim(
                session, candidate=candidate, signal_row=signal_row, claim=claim
            )
    for penalty in calculation.penalties:
        session.add(
            ScorePenalty(
                id=str(uuid4()),
                score_id=score_row.id,
                penalty_id=penalty.penalty_id,
                points=float(penalty.points),
                details=list(penalty.details),
            )
        )
    session.flush()
    previous = session.scalar(
        select(CandidateScore).where(
            CandidateScore.candidate_id == candidate.id,
            CandidateScore.is_current.is_(True),
        )
    )
    if previous is not None:
        previous.is_current = False
        previous.superseded_at = now
        session.flush()
    score_row.is_current = True
    session.flush()
    return score_row


def calculate_and_persist(
    session: Session,
    *,
    candidate: Candidate,
    brief: RoleBrief,
    config: ScoringConfig,
) -> CandidateScore:
    kernel_brief = load_kernel_brief(session, brief)
    kernel_config = load_kernel_config(config)
    active = active_signal_ids(kernel_brief)
    snapshot, sections = build_snapshot(session, candidate, active)
    calculation = calculate_score(
        kernel_brief,
        kernel_config,
        snapshot,
        stage=(
            ScoreStage.ENRICHED
            if candidate.stage == "stage2"
            else ScoreStage.PROVISIONAL
        ),
    )
    fingerprint, payload = input_fingerprint(
        candidate=candidate,
        brief=brief,
        kernel_brief=kernel_brief,
        config=config,
        active=active,
        snapshot=snapshot,
        sections=sections,
    )
    current = session.scalar(
        select(CandidateScore).where(
            CandidateScore.candidate_id == candidate.id,
            CandidateScore.is_current.is_(True),
        )
    )
    if current is not None and current.input_fingerprint == fingerprint:
        return current
    return persist_calculation(
        session,
        candidate=candidate,
        brief=brief,
        config=config,
        calculation=calculation,
        fingerprint=fingerprint,
        fingerprint_payload=payload,
        source_sections=sections,
    )
