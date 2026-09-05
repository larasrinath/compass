from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError

from linkedin_dashboard.api._filters import sanitize_for_frontend
from linkedin_dashboard.db.models import (
    AuditLog,
    Candidate,
    DashboardSession,
    Job,
    JobAttempt,
    ParsedField,
    ProfileFetch,
    ProfileIdentityObservation,
    ProfileSection,
    RoleBrief,
    SectionError,
    SectionReference,
)
from linkedin_dashboard.db.session import Database
from linkedin_dashboard.mcp.envelope import MCPResponseEnvelope
from linkedin_dashboard.mcp.errors import ErrorClass
from linkedin_dashboard.parsing import parse_section
from linkedin_dashboard.parsing.common import PARSER_VERSION
from linkedin_dashboard.parsing.identity import normalize_person_reference
from linkedin_dashboard.queue.jobs import (
    JobKind,
    PersonProfilePayload,
    persisted_payload,
    validate_payload,
)
from linkedin_dashboard.queue.worker import DurableJobQueue, JobResultProcessor

if TYPE_CHECKING:
    from linkedin_dashboard.services.scoring_service import ScoringService

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


class ProfileContractError(ValueError):
    pass


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


def profile_urn_routing_allowed(session: Any, candidate: Candidate) -> bool:
    if candidate.profile_urn is None or candidate.profile_urn_quarantined:
        return False
    accepted = any(
        _identity_observation_is_attested(session, observation)
        for observation in session.scalars(
            select(ProfileIdentityObservation).where(
                ProfileIdentityObservation.candidate_id == candidate.id,
                ProfileIdentityObservation.verdict == "accepted",
                ProfileIdentityObservation.observed_urn == candidate.profile_urn,
            )
        )
    )
    divergent = session.scalar(
        select(ProfileIdentityObservation.id)
        .where(
            ProfileIdentityObservation.candidate_id == candidate.id,
            (
                ProfileIdentityObservation.verdict.in_(("conflict", "url_mismatch"))
                | (
                    ProfileIdentityObservation.observed_urn.is_not(None)
                    & (ProfileIdentityObservation.observed_urn != candidate.profile_urn)
                )
            ),
        )
        .limit(1)
    )
    return accepted and divergent is None


def _attested_payload(raw: dict[str, Any]) -> tuple[dict[str, Any], str]:
    structured = raw.get("structuredContent")
    if not isinstance(structured, dict):
        raise ProfileContractError("profile response has no structured content")
    wrapped = structured.get("result")
    if len(structured) == 1 and isinstance(wrapped, dict):
        return wrapped, "wrapped_result"
    return structured, "structured_content"


def _identity_observation_is_attested(
    session: Any, observation: ProfileIdentityObservation
) -> bool:
    fetch = session.get(ProfileFetch, observation.fetch_id)
    candidate = session.get(Candidate, observation.candidate_id)
    if (
        fetch is None
        or candidate is None
        or fetch.candidate_id != observation.candidate_id
        or not isinstance(observation.returned_url, str)
        or fetch.contract_error is not None
        or not isinstance(fetch.raw_response, dict)
        or not isinstance(fetch.projection_payload, dict)
    ):
        return False
    committed_attempt = session.scalar(
        select(JobAttempt.id)
        .where(
            JobAttempt.job_id == fetch.job_id,
            JobAttempt.raw_response == fetch.raw_response,
        )
        .limit(1)
    )
    if committed_attempt is None:
        return False
    try:
        payload, source = _attested_payload(fetch.raw_response)
        returned_username = normalize_person_reference(observation.returned_url)
    except (ProfileContractError, ValueError):
        return False
    return (
        source == fetch.projection_source
        and payload == fetch.projection_payload
        and returned_username.casefold() == candidate.username.casefold()
        and payload.get("url") == observation.returned_url
        and payload.get("profile_urn") == observation.observed_urn
    )


def _validate_profile_payload(
    payload: dict[str, Any], *, username: str, requested_sections: list[str]
) -> None:
    returned_url = payload.get("url")
    if not isinstance(returned_url, str):
        raise ProfileContractError("profile response URL is missing or malformed")
    try:
        returned_username = normalize_person_reference(returned_url)
    except ValueError as error:
        raise ProfileContractError("profile response URL is not canonical") from error
    if returned_username.casefold() != username.casefold():
        raise ProfileContractError("profile response URL identifies another candidate")

    allowed = set(requested_sections)
    sections = payload.get("sections")
    if not isinstance(sections, dict) or any(
        not isinstance(name, str)
        or name not in allowed
        or not isinstance(raw_text, str)
        for name, raw_text in sections.items()
    ):
        raise ProfileContractError("profile sections have an invalid shape")

    errors = payload.get("section_errors", {})
    if not isinstance(errors, dict) or any(
        not isinstance(name, str)
        or name not in allowed
        or not isinstance(error, dict)
        or not isinstance(error.get("error_type"), str)
        or not isinstance(error.get("error_message"), str)
        for name, error in errors.items()
    ):
        raise ProfileContractError("profile section errors have an invalid shape")
    if set(sections).intersection(errors):
        raise ProfileContractError("profile section success and error conflict")

    references = payload.get("references", {})
    if not isinstance(references, dict):
        raise ProfileContractError("profile references have an invalid shape")
    for name, items in references.items():
        if (
            not isinstance(name, str)
            or name not in allowed
            or not isinstance(items, list)
        ):
            raise ProfileContractError("profile references have an invalid shape")
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("kind"), str):
                raise ProfileContractError("profile references have an invalid shape")
            for field in ("url", "text", "context", "value"):
                if item.get(field) is not None and not isinstance(item[field], str):
                    raise ProfileContractError(
                        "profile references have an invalid shape"
                    )

    unknown = payload.get("unknown_sections", [])
    if not isinstance(unknown, list) or any(
        not isinstance(name, str) for name in unknown
    ):
        raise ProfileContractError("unknown profile sections have an invalid shape")
    if unknown:
        raise ProfileContractError("profile response contains unknown sections")
    if payload.get("profile_urn") is not None and not isinstance(
        payload["profile_urn"], str
    ):
        raise ProfileContractError("profile URN has an invalid shape")


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

    def __init__(
        self, database: Database, scoring_service: ScoringService | None = None
    ) -> None:
        self.database = database
        self.scoring_service = scoring_service

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
        try:
            envelope = MCPResponseEnvelope.model_validate(raw)
            committed = envelope.result_payload()
            attested, source = _attested_payload(raw)
            if attested != committed:
                raise ProfileContractError("profile payload attestation disagrees")
        except (ValidationError, ValueError) as error:
            fetch_id = self._persist_fetch_raw(
                job_id,
                raw,
                projection_payload=None,
                projection_source=None,
                contract_error="malformed_profile_envelope",
            )
            self._mark_contract_failure(fetch_id, str(error))
            return
        contract_error: ProfileContractError | None = None
        url_mismatch = False
        with self.database.sessions() as session:
            fetch, candidate = self._ensure_fetch(session, job_id)
            if fetch is None or candidate is None:
                raise LookupError("profile fetch has no matching candidate")
            try:
                _validate_profile_payload(
                    attested,
                    username=candidate.username,
                    requested_sections=fetch.requested_sections,
                )
            except ProfileContractError as error:
                contract_error = error
                returned_url = attested.get("url")
                if isinstance(returned_url, str):
                    try:
                        returned_username = normalize_person_reference(returned_url)
                    except ValueError:
                        pass
                    else:
                        url_mismatch = (
                            returned_username.casefold()
                            != candidate.username.casefold()
                        )
        fetch_id = self._persist_fetch_raw(
            job_id,
            raw,
            projection_payload=attested,
            projection_source=source,
            contract_error=(
                "profile_contract_error"
                if contract_error is not None and not url_mismatch
                else None
            ),
        )
        if contract_error is not None:
            logger.error(
                "Profile contract failure for fetch %s: %s", fetch_id, contract_error
            )
            self._mark_contract_failure(
                fetch_id,
                str(contract_error),
                result=attested if url_mismatch else None,
            )
            return
        self._parse_fetch(fetch_id, attested)

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
        self,
        job_id: str,
        raw: dict[str, Any],
        *,
        projection_payload: dict[str, Any] | None,
        projection_source: str | None,
        contract_error: str | None,
    ) -> str:
        # Separate commit: profile_fetch.raw_response exists durably before any
        # section or parsed-field row can be constructed.
        with self.database.sessions.begin() as session:
            fetch, _ = self._ensure_fetch(session, job_id)
            if fetch is None:
                raise LookupError("profile fetch has no matching candidate")
            if fetch.raw_response is None:
                fetch.raw_response = raw
                fetch.projection_payload = projection_payload
                fetch.projection_source = projection_source
                fetch.contract_error = contract_error
            return fetch.id

    def _parse_fetch(self, fetch_id: str, result: dict[str, Any]) -> None:
        # Acquire before opening the write transaction. This ordering is shared
        # with brief/config changes and prevents lock/SQLite writer inversion.
        with self.database.transition_lock:
            self._parse_fetch_locked(fetch_id, result)

    def _parse_fetch_locked(self, fetch_id: str, result: dict[str, Any]) -> None:
        with self.database.sessions.begin() as session:
            fetch = session.get(ProfileFetch, fetch_id)
            if fetch is None or fetch.processed_at is not None:
                return
            candidate = session.get(Candidate, fetch.candidate_id)
            if candidate is None:
                raise LookupError("profile candidate disappeared")
            now = _now()
            fetch.returned_url = result["url"]
            self._observe_identity(session, fetch, candidate, result, now)
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
                    content_sha256=hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
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
                safe.pop("error_type", None)
                safe.pop("error_message", None)
                error_type = str(error_value["error_type"])
                error_message = str(error_value["error_message"])
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
                        source_item=error_value,
                    )
                )

            references_value = result.get("references")
            references = references_value if isinstance(references_value, dict) else {}
            for section_name, items in references.items():
                if not isinstance(items, list):
                    continue
                for source_position, item in enumerate(items):
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
                            source_position=source_position,
                        )
                    )

            returned = {name for name in sections if name in PERSON_SECTIONS}
            root_fetch = session.get(ProfileFetch, fetch.root_fetch_id)
            original_stage = (
                root_fetch.request_stage
                if root_fetch is not None
                else fetch.request_stage
            )
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
            if status == "ok":
                if original_stage == "stage1" and candidate.stage == "discovered":
                    candidate.stage = "stage1"
                elif original_stage == "stage2":
                    candidate.stage = "stage2"
            candidate.retrieval_status = status
            fetch.outcome = outcome
            fetch.finished_at = now
            fetch.duration_ms = _duration_ms(fetch.started_at, now)
            fetch.processed_at = now
            if self.scoring_service is not None:
                # The session disables autoflush.  Make the new immutable
                # sections, parsed spans, and error lineage visible to the
                # score built in this same transaction.
                session.flush()
                has_brief = session.scalar(
                    select(RoleBrief.id).where(
                        RoleBrief.session_id == candidate.session_id,
                        RoleBrief.superseded_at.is_(None),
                    )
                )
                if has_brief is not None:
                    self.scoring_service.rescore_candidate_in_session(
                        session, candidate.id
                    )

    def _mark_contract_failure(
        self,
        fetch_id: str,
        reason: str,
        *,
        result: dict[str, Any] | None = None,
    ) -> None:
        with self.database.sessions.begin() as session:
            fetch = session.get(ProfileFetch, fetch_id)
            if fetch is None or fetch.processed_at is not None:
                return
            candidate = session.get(Candidate, fetch.candidate_id)
            if candidate is None:
                return
            if result is not None:
                self._observe_url_mismatch(session, fetch, candidate, result, _now())
            self._mark_contract_failure_in_session(
                session, fetch, candidate, reason, _now()
            )

    @staticmethod
    def _observe_url_mismatch(
        session: Any,
        fetch: ProfileFetch,
        candidate: Candidate,
        result: dict[str, Any],
        now: str,
    ) -> None:
        observed_urn = (
            result.get("profile_urn")
            if isinstance(result.get("profile_urn"), str)
            else None
        )
        session.add(
            ProfileIdentityObservation(
                id=str(uuid4()),
                candidate_id=candidate.id,
                fetch_id=fetch.id,
                returned_url=result["url"],
                observed_urn=observed_urn,
                verdict="url_mismatch",
                observed_at=now,
            )
        )
        session.flush()
        candidate.profile_urn_quarantined = True
        candidate.profile_contract_error = (
            candidate.profile_contract_error or "profile_url_mismatch"
        )

    @staticmethod
    def _mark_contract_failure_in_session(
        session: Any,
        fetch: ProfileFetch,
        candidate: Candidate,
        reason: str,
        now: str,
    ) -> None:
        fetch.outcome = "error"
        fetch.finished_at = now
        fetch.duration_ms = _duration_ms(fetch.started_at, now)
        fetch.processed_at = now
        candidate.retrieval_status = "failed"
        candidate.profile_contract_error = (
            candidate.profile_contract_error or "profile_contract_error"
        )
        job = session.get(Job, fetch.job_id)
        session.add(
            AuditLog(
                id=str(uuid4()),
                session_id=candidate.session_id,
                at=now,
                actor="system",
                action="profile_contract_failure",
                subject_type="profile_fetch",
                subject_id=fetch.id,
                detail={"reason": reason},
                correlation_id=job.correlation_id if job is not None else "system",
            )
        )

    @staticmethod
    def _observe_identity(
        session: Any,
        fetch: ProfileFetch,
        candidate: Candidate,
        result: dict[str, Any],
        now: str,
    ) -> None:
        observed_urn = result.get("profile_urn")
        if observed_urn is None:
            verdict = "missing"
        elif candidate.profile_urn is None and not candidate.profile_urn_quarantined:
            verdict = "accepted"
        elif candidate.profile_urn == observed_urn:
            verdict = "same"
        else:
            verdict = "conflict"

        observation = ProfileIdentityObservation(
            id=str(uuid4()),
            candidate_id=candidate.id,
            fetch_id=fetch.id,
            returned_url=result["url"],
            observed_urn=observed_urn,
            verdict=verdict,
            observed_at=now,
        )
        session.add(observation)
        session.flush()
        if verdict == "accepted":
            claimed = session.execute(
                update(Candidate)
                .where(
                    Candidate.id == candidate.id,
                    Candidate.profile_urn.is_(None),
                    Candidate.profile_urn_quarantined.is_(False),
                )
                .values(profile_urn=observed_urn)
            )
            if not isinstance(claimed, CursorResult) or claimed.rowcount != 1:
                raise RuntimeError("profile URN observation lost its assignment race")
            candidate.profile_urn = observed_urn
        elif verdict == "conflict":
            session.execute(
                update(Candidate)
                .where(Candidate.id == candidate.id)
                .values(
                    profile_urn_quarantined=True,
                    profile_contract_error=func.coalesce(
                        Candidate.profile_contract_error, "profile_urn_conflict"
                    ),
                )
            )
            candidate.profile_urn_quarantined = True
            candidate.profile_contract_error = (
                candidate.profile_contract_error or "profile_urn_conflict"
            )
        job = session.get(Job, fetch.job_id)
        session.add(
            AuditLog(
                id=str(uuid4()),
                session_id=candidate.session_id,
                at=now,
                actor="system",
                action="profile_identity_observed",
                subject_type="profile_fetch",
                subject_id=fetch.id,
                detail={"verdict": verdict},
                correlation_id=job.correlation_id if job is not None else "system",
            )
        )


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
            if requested != list(STAGE_ONE_SECTIONS) and candidate.stage not in {
                "stage1",
                "stage2",
            }:
                raise ValueError("Stage 2 requires a completed Stage 1 retrieval")
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
            queued_candidate = db.get(Candidate, candidate_id)
            if queued_candidate is None:
                raise LookupError("candidate does not exist")
            queued_candidate.retrieval_status = "pending"
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

        try:
            return await self.queue.enqueue(
                session_id,
                JobKind.GET_PERSON_PROFILE,
                persisted_payload(payload),
                related_factory=related,
            )
        except IntegrityError as error:
            raise RuntimeError(
                "candidate already has a queued or running fetch"
            ) from error

    async def enqueue_batch(
        self,
        candidate_ids: list[str],
        sections: list[str],
        *,
        transaction_callback: Callable[[Any], None] | None = None,
        authorize_profile_reads: bool = False,
        new_only: bool = False,
    ) -> list[str]:
        if len(candidate_ids) > 1000:
            raise ValueError("a download batch supports at most 1000 profiles")
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
            if requested != list(STAGE_ONE_SECTIONS) and any(
                candidate.stage not in {"stage1", "stage2"} for candidate in candidates
            ):
                raise ValueError("Stage 2 requires a completed Stage 1 retrieval")
            session_ids = {candidate.session_id for candidate in candidates}
            if len(session_ids) != 1:
                raise ValueError("batch candidates must belong to one session")
            dashboard_session = db.get(DashboardSession, candidates[0].session_id)
            if dashboard_session is None:
                raise LookupError("session does not exist")
            required = len(candidate_ids) * (1 + len(requested))
            remaining = dashboard_session.nav_budget - dashboard_session.nav_used
            if required > remaining and not authorize_profile_reads:
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
        usernames = {candidate.id: candidate.username for candidate in candidates}
        requests: list[tuple[JobKind, dict[str, Any], Any]] = []
        for candidate_id in candidate_ids:
            username = usernames[candidate_id]
            payload = PersonProfilePayload(
                linkedin_username=username,
                sections=requested,
            )

            def related(
                db: Any,
                job: Job,
                *,
                candidate_id: str = candidate_id,
                username: str = username,
            ) -> None:
                fetch_id = str(uuid4())
                candidate = db.get(Candidate, candidate_id)
                if candidate is None:
                    raise LookupError("candidate does not exist")
                if (
                    new_only
                    and db.scalar(
                        select(ProfileFetch.id)
                        .where(ProfileFetch.candidate_id == candidate_id)
                        .limit(1)
                    )
                    is not None
                ):
                    raise IntegrityError(
                        "candidate was already requested",
                        {},
                        ValueError("already requested"),
                    )
                candidate.retrieval_status = "pending"
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
                            "stage1"
                            if requested == list(STAGE_ONE_SECTIONS)
                            else "stage2"
                        ),
                        parent_fetch_id=None,
                        root_fetch_id=fetch_id,
                    )
                )

            requests.append(
                (
                    JobKind.GET_PERSON_PROFILE,
                    persisted_payload(payload),
                    related,
                )
            )
        try:
            return await self.queue.enqueue_many(
                candidates[0].session_id,
                requests,
                transaction_callback=transaction_callback,
                authorize_profile_reads=authorize_profile_reads,
            )
        except IntegrityError as error:
            raise RuntimeError(
                "one or more candidates already have an active fetch"
            ) from error
