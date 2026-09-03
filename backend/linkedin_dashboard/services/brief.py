from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select, update

from linkedin_dashboard.audit import append_audit_event
from linkedin_dashboard.db.models import (
    BriefSkill,
    BriefTerm,
    CandidateScore,
    DashboardSession,
    RoleBrief,
)
from linkedin_dashboard.db.session import Database

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
        key = term.casefold()
        if key in seen:
            continue
        seen.add(key)
        aliases: list[str] = []
        alias_seen = {key}
        for raw_alias in item.aliases:
            alias = raw_alias.strip()
            alias_key = alias.casefold()
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
        key = cleaned.casefold()
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
    )
    if not normalized.job_description:
        raise ValueError("job_description is required")
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
    words = " ".join(re.findall(r"[^\W_]+", entered.casefold()))
    return (
        any(
            re.search(rf"(?:^|\s){re.escape(term)}(?:$|\s)", words)
            for term in PROTECTED_TERMS
        )
        or re.fullmatch(r"(?:19|20)\d{2}", words) is not None
    )


def _reject_protected(value: BriefValue) -> None:
    fields: list[tuple[str, str]] = []
    for name in (
        "required_skills",
        "optional_skills",
        "target_titles",
        "industries",
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

    offending: list[dict[str, str]] = []
    for path, entered in fields:
        if contains_protected_criterion(entered):
            offending.append({"field": path, "term": entered})
    if offending:
        raise ProtectedTermError(offending)


class BriefService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create_session(self, label: str, nav_budget: int = 120) -> DashboardSession:
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
        append_audit_event(
            self.database,
            session_id=row.id,
            actor="operator",
            action="session.created",
            subject_type="session",
            subject_id=row.id,
            detail={"label": cleaned, "nav_budget": nav_budget},
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
                .where(RoleBrief.session_id == session_id)
                .order_by(RoleBrief.version.desc())
                .limit(1)
            )
            if row is not None:
                session.expunge(row)
            return row

    def save(self, session_id: str, value: BriefValue) -> tuple[RoleBrief, int]:
        value = normalize_brief(value)
        now = datetime.now(UTC).isoformat()
        stale_count = 0
        with self.database.sessions.begin() as session:
            if session.get(DashboardSession, session_id) is None:
                raise LookupError("session does not exist")
            current = session.scalar(
                select(RoleBrief)
                .where(RoleBrief.session_id == session_id)
                .order_by(RoleBrief.version.desc())
                .limit(1)
            )
            version = 1 if current is None else current.version + 1
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
                session.execute(
                    update(CandidateScore)
                    .where(
                        CandidateScore.brief_id == current.id,
                        CandidateScore.is_current.is_(True),
                    )
                    .values(is_current=False, superseded_at=now)
                )
            brief = RoleBrief(
                id=str(uuid4()),
                session_id=session_id,
                version=version,
                created_at=now,
                superseded_at=None,
                job_description=value.job_description,
                target_titles=[item.term for item in value.target_titles],
                location=value.location,
                industries=[item.term for item in value.industries],
                positive_keywords=list(value.positive_keywords),
                negative_keywords=list(value.negative_keywords),
                message_tone=value.message_tone,
                weights_version="v1",
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
                            term_key=item.term.casefold(),
                            aliases=list(item.aliases),
                            position=position,
                        )
                    )
        append_audit_event(
            self.database,
            session_id=session_id,
            actor="operator",
            action="brief.saved",
            subject_type="role_brief",
            subject_id=brief.id,
            detail={"version": brief.version, "stale_scores": stale_count},
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
            )
