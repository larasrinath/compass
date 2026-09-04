from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import func, select, update

from linkedin_dashboard.correlation import current_correlation_id
from linkedin_dashboard.db.models import (
    AuditLog,
    BriefCredential,
    BriefSkill,
    BriefTerm,
    CandidateScore,
    DashboardSession,
    RoleBrief,
)
from linkedin_dashboard.db.session import Database
from linkedin_dashboard.services.scoring.normalization import normalize_text

if TYPE_CHECKING:
    from linkedin_dashboard.services.scoring_service import ScoringService

PROTECTED_TERMS = frozenset(
    {
        "age",
        "birth year",
        "criminal history",
        "disability",
        "ethnicity",
        "family status",
        "gender",
        "gender identity",
        "graduation year",
        "graduation years",
        "health",
        "immigration status",
        "marital status",
        "national origin",
        "photograph",
        "photo",
        "photos",
        "political affiliation",
        "pregnancy",
        "race",
        "religion",
        "sexual orientation",
        "union membership",
    }
)


@dataclass(frozen=True, slots=True)
class TermValue:
    term: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BriefValue:
    job_description: str
    required_skills: tuple[TermValue, ...]
    optional_skills: tuple[TermValue, ...]
    target_titles: tuple[TermValue, ...]
    location: str
    industries: tuple[TermValue, ...]
    positive_keywords: tuple[str, ...]
    negative_keywords: tuple[str, ...]
    message_tone: str
    required_experience_months: int | None = None
    required_credentials: tuple[TermValue, ...] = ()


class ProtectedTermError(ValueError):
    def __init__(self, terms: list[dict[str, str]]) -> None:
        super().__init__("Protected attributes cannot be used as sourcing criteria")
        self.terms = terms


def _clean_terms(values: tuple[TermValue, ...]) -> tuple[TermValue, ...]:
    result: list[TermValue] = []
    seen: set[str] = set()
    for item in values:
        term = item.term.strip()
        if not term:
            continue
        key = normalize_text(term)
        if key in seen:
            continue
        seen.add(key)
        aliases: list[str] = []
        alias_seen = {key}
        for raw_alias in item.aliases:
            alias = raw_alias.strip()
            alias_key = normalize_text(alias)
            if alias and alias_key not in alias_seen:
                alias_seen.add(alias_key)
                aliases.append(alias)
        result.append(TermValue(term=term, aliases=tuple(aliases)))
    return tuple(result)


def _clean_strings(values: tuple[str, ...]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = value.strip()
        key = normalize_text(cleaned)
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return tuple(result)


def normalize_brief(value: BriefValue) -> BriefValue:
    normalized = BriefValue(
        job_description=value.job_description.strip(),
        required_skills=_clean_terms(value.required_skills),
        optional_skills=_clean_terms(value.optional_skills),
        target_titles=_clean_terms(value.target_titles),
        location=value.location.strip(),
        industries=_clean_terms(value.industries),
        positive_keywords=_clean_strings(value.positive_keywords),
        negative_keywords=_clean_strings(value.negative_keywords),
        message_tone=value.message_tone.strip(),
        required_experience_months=value.required_experience_months,
        required_credentials=_clean_terms(value.required_credentials),
    )
    if not normalized.job_description:
        raise ValueError("job_description is required")
    if normalized.required_experience_months is not None and (
        type(normalized.required_experience_months) is not int
        or normalized.required_experience_months < 0
    ):
        raise ValueError("required_experience_months must be a nonnegative integer")
    if not (
        normalized.required_skills
        or normalized.optional_skills
        or normalized.target_titles
        or normalized.positive_keywords
    ):
        raise ValueError("at least one usable discovery criterion is required")
    _reject_protected(normalized)
    return normalized


def contains_protected_criterion(entered: str) -> bool:
    # Treat punctuation, including underscores, as separators so an alias
    # cannot evade the exact token/phrase blocklist through formatting.
    words = " ".join(re.findall(r"[^\W_]+", normalize_text(entered)))
    return any(
        re.search(rf"(?:^|\s){re.escape(term)}(?:$|\s)", words)
        for term in PROTECTED_TERMS
    ) or any(re.fullmatch(r"(?:19|20)\d{2}", token) for token in words.split())


def _reject_protected(value: BriefValue) -> None:
    fields: list[tuple[str, str]] = []
    for name in (
        "required_skills",
        "optional_skills",
        "target_titles",
        "industries",
        "required_credentials",
    ):
        for index, item in enumerate(getattr(value, name)):
            fields.append((f"{name}.{index}.term", item.term))
            fields.extend(
                (f"{name}.{index}.aliases.{alias_index}", alias)
                for alias_index, alias in enumerate(item.aliases)
            )
    for name in ("positive_keywords", "negative_keywords"):
        fields.extend(
            (f"{name}.{index}", term) for index, term in enumerate(getattr(value, name))
        )
    fields.append(("location", value.location))

    offending: list[dict[str, str]] = []
    for path, entered in fields:
        if contains_protected_criterion(entered):
            offending.append({"field": path, "term": entered})
    if offending:
        raise ProtectedTermError(offending)


class BriefService:
    def __init__(
        self, database: Database, scoring_service: ScoringService | None = None
    ) -> None:
        self.database = database
        self.scoring_service = scoring_service
        self._transition_lock = database.transition_lock

    def create_session(self, label: str, nav_budget: int = 120) -> DashboardSession:
        with self._transition_lock:
            return self._create_session_locked(label, nav_budget)

    def _create_session_locked(
        self, label: str, nav_budget: int = 120
    ) -> DashboardSession:
        cleaned = label.strip()
        if not cleaned:
            raise ValueError("session label is required")
        now = datetime.now(UTC)
        row = DashboardSession(
            id=str(uuid4()),
            created_at=now.isoformat(),
            label=cleaned,
            purge_after=(now + timedelta(days=30)).isoformat(),
            nav_budget=nav_budget,
            nav_used=0,
            send_enabled=False,
        )
        with self.database.sessions.begin() as session:
            session.add(row)
            session.flush()
            if self.scoring_service is not None:
                self.scoring_service.ensure_default_config(session, row.id)
            session.add(
                AuditLog(
                    session_id=row.id,
                    at=now.isoformat(),
                    actor="operator",
                    action="session.created",
                    subject_type="session",
                    subject_id=row.id,
                    detail={"label": cleaned, "nav_budget": nav_budget},
                    correlation_id=current_correlation_id(),
                )
            )
        return row

    def current_session(self) -> DashboardSession | None:
        with self.database.sessions() as session:
            row = session.scalar(
                select(DashboardSession)
                .where(DashboardSession.id != "00000000-0000-0000-0000-000000000000")
                .order_by(
                    DashboardSession.created_at.desc(), DashboardSession.id.desc()
                )
                .limit(1)
            )
            if row is not None:
                session.expunge(row)
            return row

    def current(self, session_id: str) -> RoleBrief | None:
        with self.database.sessions() as session:
            row = session.scalar(
                select(RoleBrief)
                .where(
                    RoleBrief.session_id == session_id,
                    RoleBrief.superseded_at.is_(None),
                )
                .order_by(RoleBrief.version.desc())
                .limit(1)
            )
            if row is not None:
                session.expunge(row)
            return row

    def save(self, session_id: str, value: BriefValue) -> tuple[RoleBrief, int]:
        # SQLite serializes writers, but a process-local lock prevents two
        # deferred transactions from both choosing the same next version and
        # turning an optimistic conflict into an opaque "database is locked".
        with self._transition_lock:
            return self._save_locked(session_id, value)

    def _save_locked(self, session_id: str, value: BriefValue) -> tuple[RoleBrief, int]:
        value = normalize_brief(value)
        now = datetime.now(UTC).isoformat()
        stale_count = 0
        with self.database.sessions.begin() as session:
            if session.get(DashboardSession, session_id) is None:
                raise LookupError("session does not exist")
            current = session.scalar(
                select(RoleBrief)
                .where(
                    RoleBrief.session_id == session_id,
                    RoleBrief.superseded_at.is_(None),
                )
                .order_by(RoleBrief.version.desc())
                .limit(1)
            )
            version = 1 if current is None else current.version + 1
            previous_had_credentials = bool(
                current
                and session.scalar(
                    select(func.count(BriefCredential.id)).where(
                        BriefCredential.brief_id == current.id
                    )
                )
            )
            config = (
                self.scoring_service.ensure_default_config(session, session_id)
                if self.scoring_service is not None
                else None
            )
            if current is not None:
                current.superseded_at = now
                stale_count = int(
                    session.scalar(
                        select(func.count(CandidateScore.id)).where(
                            CandidateScore.brief_id == current.id,
                            CandidateScore.is_current.is_(True),
                        )
                    )
                    or 0
                )
                if self.scoring_service is None:
                    session.execute(
                        update(CandidateScore)
                        .where(
                            CandidateScore.brief_id == current.id,
                            CandidateScore.is_current.is_(True),
                        )
                        .values(is_current=False, superseded_at=now)
                    )
                # The BEFORE INSERT collision guard must observe the prior
                # version's explicit lifecycle transition before the new row.
                session.flush()
            brief = RoleBrief(
                id=str(uuid4()),
                session_id=session_id,
                version=version,
                created_at=now,
                sealed_at=None,
                superseded_at=None,
                job_description=value.job_description,
                target_titles=[item.term for item in value.target_titles],
                location=value.location,
                industries=[item.term for item in value.industries],
                positive_keywords=list(value.positive_keywords),
                negative_keywords=list(value.negative_keywords),
                message_tone=value.message_tone,
                required_experience_months=value.required_experience_months,
                weights_version=str(config.version) if config is not None else "v1",
            )
            session.add(brief)
            session.flush()
            for kind, values in (
                ("required", value.required_skills),
                ("optional", value.optional_skills),
            ):
                for position, item in enumerate(values):
                    session.add(
                        BriefSkill(
                            id=str(uuid4()),
                            brief_id=brief.id,
                            term=item.term,
                            kind=kind,
                            aliases=list(item.aliases),
                            position=position,
                        )
                    )
            for kind, values in (
                ("target_title", value.target_titles),
                ("industry", value.industries),
            ):
                for position, item in enumerate(values):
                    session.add(
                        BriefTerm(
                            id=str(uuid4()),
                            brief_id=brief.id,
                            kind=kind,
                            term=item.term,
                            term_key=normalize_text(item.term),
                            aliases=list(item.aliases),
                            position=position,
                        )
                    )
            for position, item in enumerate(value.required_credentials):
                session.add(
                    BriefCredential(
                        id=str(uuid4()),
                        brief_id=brief.id,
                        term=item.term,
                        term_key=normalize_text(item.term),
                        aliases=list(item.aliases),
                        position=position,
                    )
                )
            session.flush()
            brief.sealed_at = now
            if self.scoring_service is not None:
                self.scoring_service.on_brief_saved(
                    session,
                    previous=current,
                    current=brief,
                    removed_final_credential=(
                        previous_had_credentials and not value.required_credentials
                    ),
                )
            session.add(
                AuditLog(
                    session_id=session_id,
                    at=now,
                    actor="operator",
                    action="brief.saved",
                    subject_type="role_brief",
                    subject_id=brief.id,
                    detail={"version": brief.version, "stale_scores": stale_count},
                    correlation_id=current_correlation_id(),
                )
            )
        return brief, stale_count

    def load_value(self, brief_id: str) -> BriefValue:
        with self.database.sessions() as session:
            brief = session.get(RoleBrief, brief_id)
            if brief is None:
                raise LookupError("brief does not exist")
            skills = list(
                session.scalars(
                    select(BriefSkill)
                    .where(BriefSkill.brief_id == brief_id)
                    .order_by(BriefSkill.kind, BriefSkill.position, BriefSkill.id)
                )
            )
            terms = list(
                session.scalars(
                    select(BriefTerm)
                    .where(BriefTerm.brief_id == brief_id)
                    .order_by(BriefTerm.kind, BriefTerm.position, BriefTerm.id)
                )
            )
            credentials = list(
                session.scalars(
                    select(BriefCredential)
                    .where(BriefCredential.brief_id == brief_id)
                    .order_by(BriefCredential.position, BriefCredential.id)
                )
            )
            return BriefValue(
                job_description=brief.job_description,
                required_skills=tuple(
                    TermValue(row.term, tuple(row.aliases))
                    for row in skills
                    if row.kind == "required"
                ),
                optional_skills=tuple(
                    TermValue(row.term, tuple(row.aliases))
                    for row in skills
                    if row.kind == "optional"
                ),
                target_titles=tuple(
                    TermValue(row.term, tuple(row.aliases))
                    for row in terms
                    if row.kind == "target_title"
                ),
                location=brief.location,
                industries=tuple(
                    TermValue(row.term, tuple(row.aliases))
                    for row in terms
                    if row.kind == "industry"
                ),
                positive_keywords=tuple(brief.positive_keywords),
                negative_keywords=tuple(brief.negative_keywords),
                message_tone=brief.message_tone,
                required_experience_months=brief.required_experience_months,
                required_credentials=tuple(
                    TermValue(row.term, tuple(row.aliases)) for row in credentials
                ),
            )
