from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import func, select

from linkedin_dashboard.api._filters import sanitize_for_frontend
from linkedin_dashboard.db.models import (
    Candidate,
    DashboardSession,
    Job,
    JobAttempt,
    ParsedField,
    ProfileFetch,
    ProfileSection,
    SectionError,
    SectionReference,
)
from linkedin_dashboard.db.session import Database
from linkedin_dashboard.mcp.envelope import MCPResponseEnvelope
from linkedin_dashboard.mcp.errors import ErrorClass
from linkedin_dashboard.parsing import parse_section
from linkedin_dashboard.parsing.common import PARSER_VERSION
from linkedin_dashboard.queue.jobs import (
    JobKind,
    PersonProfilePayload,
    persisted_payload,
    validate_payload,
)
from linkedin_dashboard.queue.worker import DurableJobQueue, JobResultProcessor

# Pinned by the sibling Tier-1 contract test.  Runtime code intentionally does
# not import the upstream server (D-01).
PERSON_SECTIONS = (
    "main_profile",
    "experience",
    "education",
    "interests",
    "honors",
    "languages",
    "certifications",
    "skills",
    "projects",
    "contact_info",
    "posts",
)
EXTRA_PERSON_SECTIONS = PERSON_SECTIONS[1:]
STAGE_ONE_SECTIONS = ("experience",)
MAX_PROMOTED_SECTIONS = 3
logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _safe_mapping(value: object) -> dict[str, Any]:
    safe = sanitize_for_frontend(value)
    return safe if isinstance(safe, dict) else {}


def canonical_sections(sections: list[str] | tuple[str, ...]) -> list[str]:
    requested = set(sections)
    unknown = requested.difference(EXTRA_PERSON_SECTIONS)
    if unknown:
        raise ValueError("Unknown profile sections: " + ", ".join(sorted(unknown)))
    return [name for name in EXTRA_PERSON_SECTIONS if name in requested]


class CompositeResultProcessor:
    def __init__(self, *processors: JobResultProcessor) -> None:
        self.processors = processors

    def reconcile(self) -> None:
        for processor in self.processors:
            processor.reconcile()

    def process_result(
        self, job_id: str, kind: JobKind, result: dict[str, Any]
    ) -> None:
        for processor in self.processors:
            processor.process_result(job_id, kind, result)

    def process_failure(
        self, job_id: str, kind: JobKind, error_class: ErrorClass
    ) -> None:
        for processor in self.processors:
            processor.process_failure(job_id, kind, error_class)


class EnrichmentResultProcessor:
    """Project only committed profile envelopes into immutable history."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def reconcile(self) -> None:
        with self.database.sessions() as session:
            jobs = list(
                session.scalars(
                    select(Job).where(Job.kind == JobKind.GET_PERSON_PROFILE.value)
                )
            )
            work = [(job.id, job.state, job.error) for job in jobs]
        for job_id, state, error in work:
            raw = self._raw_for_job(job_id)
            if raw is not None:
                try:
                    self.process_result(job_id, JobKind.GET_PERSON_PROFILE, {})
                except Exception:
                    # Captured data is never replayed because of a local parser bug.
                    continue
            elif state in {"failed", "interrupted", "cancelled"}:
                try:
                    error_class = ErrorClass(error) if error else ErrorClass.UNKNOWN
                except ValueError:
                    error_class = ErrorClass.UNKNOWN
                self.process_failure(job_id, JobKind.GET_PERSON_PROFILE, error_class)

    def process_result(
        self, job_id: str, kind: JobKind, result: dict[str, Any]
    ) -> None:
        del result
        if kind is not JobKind.GET_PERSON_PROFILE:
            return
        raw = self._raw_for_job(job_id)
        if raw is None:
            raise RuntimeError("profile projection requires a committed raw response")
        envelope = MCPResponseEnvelope.model_validate(raw)
        committed = envelope.result_payload()
        fetch_id = self._persist_fetch_raw(job_id, raw, committed)
        self._parse_fetch(fetch_id, committed)

    def process_failure(
        self, job_id: str, kind: JobKind, error_class: ErrorClass
    ) -> None:
        if kind is not JobKind.GET_PERSON_PROFILE:
            return
        with self.database.sessions.begin() as session:
            fetch, candidate = self._ensure_fetch(session, job_id)
            if fetch is None or candidate is None or fetch.processed_at is not None:
                return
            now = _now()
            fetch.outcome = "error"
            fetch.finished_at = now
            fetch.duration_ms = _duration_ms(fetch.started_at, now)
            fetch.processed_at = now
            candidate.retrieval_status = (
                "rate_limited" if error_class is ErrorClass.RATE_LIMIT else "failed"
            )

    def _raw_for_job(self, job_id: str) -> dict[str, Any] | None:
        with self.database.sessions() as session:
            attempt = session.scalar(
                select(JobAttempt)
                .where(
                    JobAttempt.job_id == job_id,
                    JobAttempt.raw_response.is_not(None),
                )
                .order_by(JobAttempt.attempt_number.desc())
                .limit(1)
            )
            return attempt.raw_response if attempt is not None else None

    def _ensure_fetch(
        self, session: Any, job_id: str
    ) -> tuple[ProfileFetch | None, Candidate | None]:
        fetch = session.scalar(
            select(ProfileFetch).where(ProfileFetch.job_id == job_id)
        )
        if fetch is not None:
            return fetch, session.get(Candidate, fetch.candidate_id)
        job = session.get(Job, job_id)
        if job is None:
            return None, None
        try:
            payload = validate_payload(JobKind.GET_PERSON_PROFILE, job.payload or {})
        except (ValueError, ValidationError):
            return None, None
        if not isinstance(payload, PersonProfilePayload):
            return None, None
        candidate = session.scalar(
            select(Candidate).where(
                Candidate.session_id == job.session_id,
                Candidate.username.collate("unicode_casefold")
                == payload.linkedin_username,
            )
        )
        if candidate is None:
            return None, None
        fetch_id = str(uuid4())
        parent = (
            session.scalar(
                select(ProfileFetch).where(ProfileFetch.job_id == payload.parent_job_id)
            )
            if payload.parent_job_id
            else None
        )
        if payload.parent_job_id and parent is None:
            return None, None
        fetch = ProfileFetch(
            id=fetch_id,
            candidate_id=candidate.id,
            job_id=job.id,
            tool=JobKind.GET_PERSON_PROFILE.value,
            requested_sections=["main_profile", *payload.sections],
            args={
                "linkedin_username": payload.linkedin_username,
                "sections": list(payload.sections),
                **(
                    {"max_scrolls": payload.max_scrolls}
                    if payload.max_scrolls is not None
                    else {}
                ),
            },
            started_at=job.started_at or job.queued_at,
            finished_at=None,
            duration_ms=None,
            outcome=None,
            raw_response=None,
            returned_url=None,
            processed_at=None,
            request_stage="resume" if parent is not None else "stage2",
            parent_fetch_id=parent.id if parent is not None else None,
            root_fetch_id=parent.root_fetch_id if parent is not None else fetch_id,
        )
        session.add(fetch)
        session.flush()
        return fetch, candidate

    def _persist_fetch_raw(
        self, job_id: str, raw: dict[str, Any], result: dict[str, Any]
    ) -> str:
        # Separate commit: profile_fetch.raw_response exists durably before any
        # section or parsed-field row can be constructed.
        with self.database.sessions.begin() as session:
            fetch, _ = self._ensure_fetch(session, job_id)
            if fetch is None:
                raise LookupError("profile fetch has no matching candidate")
            if fetch.raw_response is None:
                fetch.raw_response = raw
            if fetch.returned_url is None and isinstance(result.get("url"), str):
                fetch.returned_url = result["url"]
            return fetch.id

    def _parse_fetch(self, fetch_id: str, result: dict[str, Any]) -> None:
        with self.database.sessions.begin() as session:
            fetch = session.get(ProfileFetch, fetch_id)
            if fetch is None or fetch.processed_at is not None:
                return
            candidate = session.get(Candidate, fetch.candidate_id)
            if candidate is None:
                raise LookupError("profile candidate disappeared")
            now = _now()
            sections_value = result.get("sections")
            sections = sections_value if isinstance(sections_value, dict) else {}
            for section_name, raw_text in sections.items():
                if section_name not in PERSON_SECTIONS or not isinstance(raw_text, str):
                    continue
                section = ProfileSection(
                    id=str(uuid4()),
                    candidate_id=candidate.id,
                    fetch_id=fetch.id,
                    section_name=section_name,
                    raw_text=raw_text,
                    retrieved_at=now,
                    char_len=len(raw_text),
                )
                session.add(section)
                session.flush()
                for parsed in parse_section(section_name, raw_text):
                    session.add(
                        ParsedField(
                            id=str(uuid4()),
                            candidate_id=candidate.id,
                            field_key=parsed.field_key,
                            value=parsed.value,
                            section_name=section_name,
                            span_start=parsed.span.start,
                            span_end=parsed.span.end,
                            snippet=parsed.span.snippet,
                            origin=parsed.origin,
                            parser_version=PARSER_VERSION,
                            created_at=now,
                            profile_section_id=section.id,
                        )
                    )

            errors_value = result.get("section_errors")
            errors = errors_value if isinstance(errors_value, dict) else {}
            rate_limited = False
            for section_name, error_value in errors.items():
                if not isinstance(error_value, dict):
                    continue
                safe = _safe_mapping(error_value)
                error_type = str(safe.pop("error_type", "unknown"))
                error_message = str(safe.pop("error_message", "LinkedIn read failed"))
                rate_limited |= error_type.casefold() == "rate_limit"
                session.add(
                    SectionError(
                        id=str(uuid4()),
                        candidate_id=candidate.id,
                        search_run_id=None,
                        fetch_id=fetch.id,
                        section_name=str(section_name),
                        error_type=error_type,
                        error_message=error_message,
                        extra=safe,
                    )
                )

            unknown_value = result.get("unknown_sections")
            if isinstance(unknown_value, list):
                for section_name in unknown_value:
                    logger.error(
                        "MCP returned unknown profile section %r for fetch %s; "
                        "dashboard/server section contracts have drifted",
                        section_name,
                        fetch.id,
                    )
                    session.add(
                        SectionError(
                            id=str(uuid4()),
                            candidate_id=candidate.id,
                            search_run_id=None,
                            fetch_id=fetch.id,
                            section_name=str(section_name),
                            error_type="unknown_section",
                            error_message=(
                                "Dashboard sent an unsupported profile section; "
                                "this is a client bug."
                            ),
                            extra={},
                        )
                    )

            references_value = result.get("references")
            references = references_value if isinstance(references_value, dict) else {}
            for section_name, items in references.items():
                if not isinstance(items, list):
                    continue
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    session.add(
                        SectionReference(
                            id=str(uuid4()),
                            candidate_id=candidate.id,
                            section_name=str(section_name),
                            kind=str(item.get("kind", "unknown")),
                            url=str(item.get("url") or ""),
                            text=str(item["text"])
                            if item.get("text") is not None
                            else None,
                            context=(
                                str(item["context"])
                                if item.get("context") is not None
                                else None
                            ),
                            value=str(item["value"])
                            if item.get("value") is not None
                            else None,
                            fetch_id=fetch.id,
                        )
                    )

            if isinstance(result.get("profile_urn"), str):
                candidate.profile_urn = result["profile_urn"]
            returned = {name for name in sections if name in PERSON_SECTIONS}
            root_fetch = session.get(ProfileFetch, fetch.root_fetch_id)
            original_stage = (
                root_fetch.request_stage
                if root_fetch is not None
                else fetch.request_stage
            )
            if original_stage == "stage1" and candidate.stage == "discovered":
                candidate.stage = "stage1"
            elif original_stage == "stage2":
                candidate.stage = "stage2"
            if rate_limited:
                status = "rate_limited"
                outcome = "rate_limited" if not returned else "partial"
            elif errors:
                status = "partial" if returned else "failed"
                outcome = "partial" if returned else "error"
            elif returned:
                status = "ok"
                outcome = "ok"
            else:
                status = "failed"
                outcome = "error"
            candidate.retrieval_status = status
            fetch.outcome = outcome
            fetch.finished_at = now
            fetch.duration_ms = _duration_ms(fetch.started_at, now)
            fetch.processed_at = now


def _duration_ms(start: str, finish: str) -> int:
    try:
        return max(
            0,
            int(
                (
                    datetime.fromisoformat(finish) - datetime.fromisoformat(start)
                ).total_seconds()
                * 1000
            ),
        )
    except ValueError:
        return 0


class EnrichmentService:
    def __init__(self, database: Database, queue: DurableJobQueue) -> None:
        self.database = database
        self.queue = queue

    async def enqueue(self, candidate_id: str, sections: list[str]) -> str:
        requested = canonical_sections(sections)
        if not requested:
            raise ValueError("At least one extra profile section is required")
        if (
            requested != list(STAGE_ONE_SECTIONS)
            and len(requested) > MAX_PROMOTED_SECTIONS
        ):
            raise ValueError("Stage 2 supports at most 3 promoted sections per call")
        with self.database.sessions() as db:
            candidate = db.get(Candidate, candidate_id)
            if candidate is None:
                raise LookupError("candidate does not exist")
            active = db.scalar(
                select(Job.id)
                .join(ProfileFetch, ProfileFetch.job_id == Job.id)
                .where(
                    ProfileFetch.candidate_id == candidate_id,
                    Job.state.in_(("pending", "queued", "running")),
                )
                .limit(1)
            )
            if active is not None:
                raise RuntimeError("candidate already has a queued or running fetch")
            session_id = candidate.session_id
            username = candidate.username

        payload = PersonProfilePayload(
            linkedin_username=username,
            sections=requested,
        )

        def related(db: Any, job: Job) -> None:
            fetch_id = str(uuid4())
            db.add(
                ProfileFetch(
                    id=fetch_id,
                    candidate_id=candidate_id,
                    job_id=job.id,
                    tool=JobKind.GET_PERSON_PROFILE.value,
                    requested_sections=["main_profile", *requested],
                    args={
                        "linkedin_username": username,
                        "sections": requested,
                    },
                    started_at=job.queued_at,
                    finished_at=None,
                    duration_ms=None,
                    outcome=None,
                    raw_response=None,
                    returned_url=None,
                    processed_at=None,
                    request_stage=(
                        "stage1" if requested == list(STAGE_ONE_SECTIONS) else "stage2"
                    ),
                    parent_fetch_id=None,
                    root_fetch_id=fetch_id,
                )
            )

        return await self.queue.enqueue(
            session_id,
            JobKind.GET_PERSON_PROFILE,
            persisted_payload(payload),
            related_factory=related,
        )

    async def enqueue_batch(
        self, candidate_ids: list[str], sections: list[str]
    ) -> list[str]:
        if not candidate_ids or len(set(candidate_ids)) != len(candidate_ids):
            raise ValueError("candidate_ids must be a non-empty unique list")
        requested = canonical_sections(sections)
        if not requested:
            raise ValueError("At least one extra profile section is required")
        if (
            requested != list(STAGE_ONE_SECTIONS)
            and len(requested) > MAX_PROMOTED_SECTIONS
        ):
            raise ValueError("Stage 2 supports at most 3 promoted sections per call")
        with self.database.sessions() as db:
            candidates = list(
                db.scalars(select(Candidate).where(Candidate.id.in_(candidate_ids)))
            )
            if len(candidates) != len(candidate_ids):
                raise LookupError("one or more candidates do not exist")
            session_ids = {candidate.session_id for candidate in candidates}
            if len(session_ids) != 1:
                raise ValueError("batch candidates must belong to one session")
            dashboard_session = db.get(DashboardSession, candidates[0].session_id)
            if dashboard_session is None:
                raise LookupError("session does not exist")
            required = len(candidate_ids) * (1 + len(requested))
            remaining = dashboard_session.nav_budget - dashboard_session.nav_used
            if required > remaining:
                raise RuntimeError(
                    f"navigation budget shortfall: need {required}, have {remaining}"
                )
            active_count = int(
                db.scalar(
                    select(func.count(Job.id))
                    .join(ProfileFetch, ProfileFetch.job_id == Job.id)
                    .where(
                        ProfileFetch.candidate_id.in_(candidate_ids),
                        Job.state.in_(("pending", "queued", "running")),
                    )
                )
                or 0
            )
            if active_count:
                raise RuntimeError(
                    "one or more candidates already have an active fetch"
                )
        job_ids: list[str] = []
        for candidate_id in candidate_ids:
            job_ids.append(await self.enqueue(candidate_id, requested))
        return job_ids
