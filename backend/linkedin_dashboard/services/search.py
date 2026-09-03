from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import and_, func, or_, select

from linkedin_dashboard.api._filters import sanitize_for_frontend
from linkedin_dashboard.audit import append_audit_event
from linkedin_dashboard.db.models import (
    Candidate,
    CandidateReference,
    CandidateSource,
    CompanyLookup,
    Job,
    JobAttempt,
    RoleBrief,
    SearchRun,
    SectionError,
)
from linkedin_dashboard.db.session import Database
from linkedin_dashboard.mcp.envelope import MCPResponseEnvelope
from linkedin_dashboard.mcp.errors import ErrorClass
from linkedin_dashboard.parsing.identity import (
    InvalidPersonReference,
    canonical_profile_url,
    normalize_person_reference,
)
from linkedin_dashboard.queue.jobs import JobKind
from linkedin_dashboard.queue.worker import DurableJobQueue
from linkedin_dashboard.services.brief import contains_protected_criterion

MAX_SEARCHES_PER_SESSION = 40
MAX_CANDIDATES_PER_SESSION = 200


def current_company_error(value: str) -> str:
    """Mirror the server's actionable filter error without calling it."""
    return (
        "current_company must be a numeric LinkedIn company URN id "
        f"(e.g. '1115' for SAP); got {value!r}. Plain-text company names are "
        "silently ignored by LinkedIn. Look up the URN via "
        'get_company_profile -> references["about"].'
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _safe_error(value: object) -> dict[str, Any]:
    sanitized = sanitize_for_frontend(value)
    return sanitized if isinstance(sanitized, dict) else {}


def _safe_job_message(error: str | None) -> str:
    return {
        "AUTH_REQUIRED": "LinkedIn authentication is required.",
        "BROWSER_BUSY": "The LinkedIn browser is currently in use.",
        "BROWSER_SETUP": "The LinkedIn browser is not ready.",
        "RATE_LIMIT": "LinkedIn rate-limited this request.",
        "INVALID_REFERENCE": "The LinkedIn reference is invalid.",
        "PROFILE_NOT_FOUND": "The LinkedIn profile was not found.",
        "TIMEOUT": "The MCP operation timed out.",
        "TRANSPORT": "The local MCP server is unreachable.",
        "BUDGET_EXHAUSTED": "The session navigation budget is exhausted.",
    }.get(error or "", "The LinkedIn read failed.")


class DiscoveryResultProcessor:
    """Idempotent raw-to-domain projector for queued M2 read operations."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def reconcile(self) -> None:
        with self.database.sessions() as session:
            jobs = list(
                session.scalars(
                    select(Job).where(
                        Job.kind.in_(
                            (
                                JobKind.SEARCH_PEOPLE.value,
                                JobKind.GET_COMPANY_PROFILE.value,
                            )
                        )
                    )
                )
            )
            work = [(job.id, JobKind(job.kind), job.state, job.error) for job in jobs]
        for job_id, kind, state, error in work:
            raw = self._raw_for_job(job_id)
            if raw is not None:
                try:
                    envelope = MCPResponseEnvelope.model_validate(raw)
                    self.process_result(job_id, kind, envelope.result_payload())
                    if state == "interrupted":
                        self._retain_interrupted_status(job_id, kind)
                except (ValueError, ValidationError):
                    self.process_failure(job_id, kind, ErrorClass.UNKNOWN)
                except Exception:
                    # Leave processed_at unset. A parser bug must not block
                    # startup or cause the already-captured MCP call to replay.
                    continue
            elif state in {"failed", "interrupted", "cancelled"}:
                try:
                    error_class = ErrorClass(error) if error else ErrorClass.UNKNOWN
                except ValueError:
                    error_class = ErrorClass.UNKNOWN
                self.process_failure(job_id, kind, error_class)

    def _retain_interrupted_status(self, job_id: str, kind: JobKind) -> None:
        """A recovered response is usable, but a crashed owner is still uncertain."""
        with self.database.sessions.begin() as session:
            if kind is JobKind.SEARCH_PEOPLE:
                row = session.scalar(
                    select(SearchRun).where(SearchRun.job_id == job_id)
                )
            else:
                row = session.scalar(
                    select(CompanyLookup).where(CompanyLookup.job_id == job_id)
                )
            if row is not None:
                row.status = "interrupted"

    def process_result(
        self, job_id: str, kind: JobKind, result: dict[str, Any]
    ) -> None:
        raw = self._raw_for_job(job_id)
        if raw is None:
            raise RuntimeError("domain projection requires a committed raw response")
        if kind is JobKind.SEARCH_PEOPLE:
            self._persist_search_raw(job_id, raw, result)
            self._parse_search(job_id, result)
        elif kind is JobKind.GET_COMPANY_PROFILE:
            self._persist_company_raw(job_id, raw)
            self._parse_company(job_id, result)

    def process_failure(
        self, job_id: str, kind: JobKind, error_class: ErrorClass
    ) -> None:
        del error_class
        with self.database.sessions.begin() as session:
            job = session.get(Job, job_id)
            status = (
                job.state
                if job is not None and job.state in {"interrupted", "cancelled"}
                else "failed"
            )
            if kind is JobKind.SEARCH_PEOPLE:
                run = session.scalar(
                    select(SearchRun).where(SearchRun.job_id == job_id)
                )
                if run is not None and run.processed_at is None:
                    run.status = status
                    run.processed_at = _now()
            elif kind is JobKind.GET_COMPANY_PROFILE:
                lookup = session.scalar(
                    select(CompanyLookup).where(CompanyLookup.job_id == job_id)
                )
                if lookup is not None and lookup.processed_at is None:
                    lookup.status = status
                    lookup.processed_at = _now()

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

    def _persist_search_raw(
        self, job_id: str, raw: dict[str, Any], result: dict[str, Any]
    ) -> None:
        with self.database.sessions.begin() as session:
            run = session.scalar(select(SearchRun).where(SearchRun.job_id == job_id))
            if run is None:
                return
            if run.raw_response is None:
                run.raw_response = raw
            if run.result_url is None and isinstance(result.get("url"), str):
                run.result_url = str(result["url"])

    def _parse_search(self, job_id: str, result: dict[str, Any]) -> None:
        with self.database.sessions.begin() as session:
            run = session.scalar(select(SearchRun).where(SearchRun.job_id == job_id))
            if run is None or run.processed_at is not None:
                return
            references = result.get("references")
            reference_map = references if isinstance(references, dict) else {}
            search_references = reference_map.get("search_results", [])
            if not isinstance(search_references, list):
                search_references = []

            all_references: list[dict[str, Any]] = []
            for section_values in reference_map.values():
                if isinstance(section_values, list):
                    all_references.extend(
                        item for item in section_values if isinstance(item, dict)
                    )
            run.reference_count = len(all_references)
            run.person_reference_count = sum(
                1
                for item in search_references
                if isinstance(item, dict) and item.get("kind") == "person"
            )

            existing_refs = list(
                session.scalars(
                    select(CandidateReference).where(
                        CandidateReference.search_run_id == run.id
                    )
                )
            )
            if existing_refs:
                raise RuntimeError("unprocessed search already has parsed references")

            position = 0
            ref_rows: dict[int, CandidateReference] = {}
            for section_name, section_values in reference_map.items():
                if not isinstance(section_values, list):
                    continue
                for item in section_values:
                    if not isinstance(item, dict):
                        continue
                    known = {
                        "kind",
                        "url",
                        "text",
                        "context",
                        "value",
                        "position",
                        "section_name",
                    }
                    row = CandidateReference(
                        id=str(uuid4()),
                        search_run_id=run.id,
                        kind=str(item.get("kind", "unknown")),
                        url=str(item.get("url") or ""),
                        text=(
                            str(item["text"]) if item.get("text") is not None else None
                        ),
                        context=(
                            str(item["context"])
                            if item.get("context") is not None
                            else None
                        ),
                        value=(
                            str(item["value"])
                            if item.get("value") is not None
                            else None
                        ),
                        extra={
                            "section_name": str(section_name),
                            **{
                                key: value
                                for key, value in item.items()
                                if key not in known
                            },
                        },
                        position=position,
                    )
                    session.add(row)
                    if section_name == "search_results":
                        ref_rows[id(item)] = row
                    position += 1

            candidate_count = int(
                session.scalar(
                    select(func.count(Candidate.id)).where(
                        Candidate.session_id == run.session_id
                    )
                )
                or 0
            )
            invalid_errors: list[tuple[int, str]] = []
            candidate_limit_skipped = False
            for ref_position, item in enumerate(search_references):
                if not isinstance(item, dict) or item.get("kind") != "person":
                    continue
                url = item.get("url")
                if not isinstance(url, str):
                    invalid_errors.append((ref_position, "person reference has no URL"))
                    continue
                try:
                    username = normalize_person_reference(url)
                except InvalidPersonReference as error:
                    invalid_errors.append((ref_position, str(error)))
                    continue
                dedupe_key = username.casefold()
                candidate = session.scalar(
                    select(Candidate).where(
                        Candidate.session_id == run.session_id,
                        or_(
                            Candidate.dedupe_key == dedupe_key,
                            and_(
                                Candidate.dedupe_key.is_(None),
                                func.lower(Candidate.username) == username.lower(),
                            ),
                        ),
                    )
                )
                if candidate is None:
                    if candidate_count >= MAX_CANDIDATES_PER_SESSION:
                        candidate_limit_skipped = True
                        continue
                    candidate = Candidate(
                        id=str(uuid4()),
                        session_id=run.session_id,
                        username=username,
                        dedupe_key=dedupe_key,
                        profile_url=canonical_profile_url(username),
                        display_name=(
                            str(item["text"]).strip()
                            if item.get("text") is not None
                            else None
                        ),
                        profile_urn=None,
                        first_seen_at=run.created_at,
                        stage="discovered",
                        retrieval_status="pending",
                    )
                    session.add(candidate)
                    session.flush()
                    candidate_count += 1
                reference_row = ref_rows.get(id(item))
                if reference_row is None:
                    continue
                source = session.get(CandidateSource, (candidate.id, run.id))
                if source is None:
                    session.add(
                        CandidateSource(
                            candidate_id=candidate.id,
                            search_run_id=run.id,
                            candidate_ref_id=reference_row.id,
                        )
                    )

            errors = result.get("section_errors")
            error_map = errors if isinstance(errors, dict) else {}
            rate_limited = False
            for section_name, raw_error in error_map.items():
                if not isinstance(raw_error, dict):
                    continue
                safe = _safe_error(raw_error)
                error_type = str(safe.pop("error_type", "unknown"))
                error_message = str(safe.pop("error_message", "LinkedIn read failed"))
                rate_limited |= error_type.casefold() == "rate_limit"
                session.add(
                    SectionError(
                        id=str(uuid4()),
                        candidate_id=None,
                        search_run_id=run.id,
                        fetch_id=None,
                        section_name=str(section_name),
                        error_type=error_type,
                        error_message=error_message,
                        extra=safe,
                    )
                )
            for ref_position, message in invalid_errors:
                session.add(
                    SectionError(
                        id=str(uuid4()),
                        candidate_id=None,
                        search_run_id=run.id,
                        fetch_id=None,
                        section_name="search_results",
                        error_type="invalid_reference",
                        error_message=f"Reference {ref_position + 1}: {message}",
                        extra={},
                    )
                )
            if candidate_limit_skipped:
                session.add(
                    SectionError(
                        id=str(uuid4()),
                        candidate_id=None,
                        search_run_id=run.id,
                        fetch_id=None,
                        section_name="search_results",
                        error_type="candidate_limit",
                        error_message=(
                            "Session candidate limit "
                            f"({MAX_CANDIDATES_PER_SESSION}) reached"
                        ),
                        extra={},
                    )
                )
            sections = result.get("sections")
            has_text = isinstance(sections, dict) and isinstance(
                sections.get("search_results"), str
            )
            if rate_limited:
                run.status = "rate_limited"
            elif bool((run.raw_response or {}).get("isError")):
                run.status = "failed"
            elif error_map or invalid_errors:
                run.status = "partial" if has_text or all_references else "failed"
            else:
                run.status = "ok"
            run.processed_at = _now()

    def _persist_company_raw(self, job_id: str, raw: dict[str, Any]) -> None:
        with self.database.sessions.begin() as session:
            lookup = session.scalar(
                select(CompanyLookup).where(CompanyLookup.job_id == job_id)
            )
            if lookup is not None and lookup.raw_response is None:
                lookup.raw_response = raw

    def _parse_company(self, job_id: str, result: dict[str, Any]) -> None:
        with self.database.sessions.begin() as session:
            lookup = session.scalar(
                select(CompanyLookup).where(CompanyLookup.job_id == job_id)
            )
            if lookup is None or lookup.processed_at is not None:
                return
            references = result.get("references")
            about = references.get("about", []) if isinstance(references, dict) else []
            values = [
                str(item.get("value"))
                for item in about
                if isinstance(item, dict)
                and item.get("kind") == "company_urn"
                and str(item.get("value", "")).isascii()
                and str(item.get("value", "")).isdigit()
            ]
            lookup.status = (
                "failed"
                if bool((lookup.raw_response or {}).get("isError"))
                else ("ok" if values else "not_exposed")
            )
            lookup.processed_at = _now()


class SearchService:
    def __init__(
        self,
        database: Database,
        queue: DurableJobQueue,
        processor: DiscoveryResultProcessor,
    ) -> None:
        self.database = database
        self.queue = queue
        self.processor = processor

    async def enqueue_search(
        self,
        *,
        session_id: str,
        brief_id: str,
        keywords: str,
        location: str | None,
        network: list[str] | None,
        current_company: str | None,
    ) -> tuple[str, str]:
        keywords = keywords.strip()
        location = location.strip() if location else None
        network = list(dict.fromkeys(network or [])) or None
        current_company = current_company.strip() if current_company else None
        if not keywords:
            raise ValueError("keywords must not be blank")
        if contains_protected_criterion(keywords):
            raise ValueError("keywords contain a protected sourcing criterion")
        invalid_network = [
            item for item in network or [] if item not in {"F", "S", "O"}
        ]
        if invalid_network:
            raise ValueError(
                f"Invalid network token(s) {invalid_network!r}; expected any of "
                f"{['F', 'S', 'O']!r}"
            )
        if current_company is not None and (
            not current_company.isascii() or not current_company.isdigit()
        ):
            raise ValueError(current_company_error(current_company))

        with self.database.sessions() as session:
            brief = session.get(RoleBrief, brief_id)
            if brief is None or brief.session_id != session_id or brief.superseded_at:
                raise LookupError("a current saved role brief is required")
            run_count = int(
                session.scalar(
                    select(func.count(SearchRun.id)).where(
                        SearchRun.session_id == session_id
                    )
                )
                or 0
            )
            if run_count >= MAX_SEARCHES_PER_SESSION:
                raise ValueError(
                    f"session search limit ({MAX_SEARCHES_PER_SESSION}) reached"
                )
            candidate_count = int(
                session.scalar(
                    select(func.count(Candidate.id)).where(
                        Candidate.session_id == session_id
                    )
                )
                or 0
            )
            if candidate_count >= MAX_CANDIDATES_PER_SESSION:
                raise ValueError(
                    f"session candidate limit ({MAX_CANDIDATES_PER_SESSION}) reached"
                )

        run_id = str(uuid4())

        def add_run(session: Any, job: Job) -> None:
            session.add(
                SearchRun(
                    id=run_id,
                    session_id=session_id,
                    brief_id=brief_id,
                    job_id=job.id,
                    created_at=_now(),
                    keywords=keywords,
                    location=location,
                    network=network,
                    current_company=current_company,
                    result_url=None,
                    raw_response=None,
                    processed_at=None,
                    reference_count=0,
                    person_reference_count=0,
                    status="queued",
                )
            )

        job_id = await self.queue.enqueue(
            session_id,
            JobKind.SEARCH_PEOPLE,
            {
                "keywords": keywords,
                "location": location,
                "network": network,
                "current_company": current_company,
                "search_run_id": run_id,
            },
            related_factory=add_run,
        )
        append_audit_event(
            self.database,
            session_id=session_id,
            actor="operator",
            action="search.enqueued",
            subject_type="search_run",
            subject_id=run_id,
            detail={"job_id": job_id, "network": network or []},
        )
        return job_id, run_id

    async def enqueue_company_lookup(
        self, *, session_id: str, slug: str
    ) -> tuple[str, str]:
        slug = slug.strip()
        if not slug or "/" in slug or any(character.isspace() for character in slug):
            raise ValueError("company slug must be the value after /company/")
        lookup_id = str(uuid4())

        def add_lookup(session: Any, job: Job) -> None:
            session.add(
                CompanyLookup(
                    id=lookup_id,
                    session_id=session_id,
                    job_id=job.id,
                    slug=slug,
                    created_at=_now(),
                    status="queued",
                    raw_response=None,
                    processed_at=None,
                )
            )

        job_id = await self.queue.enqueue(
            session_id,
            JobKind.GET_COMPANY_PROFILE,
            {
                "company_name": slug,
                "sections": ["about"],
                "company_lookup_id": lookup_id,
            },
            related_factory=add_lookup,
        )
        return job_id, lookup_id

    def company_lookup(self, lookup_id: str) -> dict[str, Any]:
        with self.database.sessions() as session:
            lookup = session.get(CompanyLookup, lookup_id)
            if lookup is None:
                raise LookupError("company lookup does not exist")
            job = session.get(Job, lookup.job_id)
            effective_status = (
                job.state
                if job is not None
                and (
                    job.state in {"pending", "queued", "running"}
                    or lookup.processed_at is None
                )
                else lookup.status
            )
            candidates: list[dict[str, str]] = []
            if lookup.raw_response:
                try:
                    result = MCPResponseEnvelope.model_validate(
                        lookup.raw_response
                    ).result_payload()
                    references = result.get("references")
                    about = (
                        references.get("about", [])
                        if isinstance(references, dict)
                        else []
                    )
                    candidates = [
                        {
                            "urn_id": str(item["value"]),
                            "text": str(item.get("text") or item["value"]),
                        }
                        for item in about
                        if isinstance(item, dict)
                        and item.get("kind") == "company_urn"
                        and str(item.get("value", "")).isascii()
                        and str(item.get("value", "")).isdigit()
                    ]
                except (ValueError, ValidationError):
                    candidates = []
            return {
                "id": lookup.id,
                "job_id": lookup.job_id,
                "slug": lookup.slug,
                "status": effective_status,
                "candidates": candidates,
                "note": (
                    (
                        "LinkedIn did not expose a company URN; enter a numeric ID "
                        "manually."
                    )
                    if effective_status == "not_exposed"
                    else (
                        _safe_job_message(job.error if job is not None else None)
                        if effective_status in {"failed", "interrupted", "cancelled"}
                        else None
                    )
                ),
            }

    def list_runs(self, session_id: str) -> list[dict[str, Any]]:
        with self.database.sessions() as session:
            rows = list(
                session.scalars(
                    select(SearchRun)
                    .where(SearchRun.session_id == session_id)
                    .order_by(SearchRun.created_at.desc(), SearchRun.id.desc())
                )
            )
            return [self._run_dict(session, row, detail=False) for row in rows]

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self.database.sessions() as session:
            row = session.get(SearchRun, run_id)
            if row is None:
                raise LookupError("search run does not exist")
            return self._run_dict(session, row, detail=True)

    def _run_dict(
        self, session: Any, row: SearchRun, *, detail: bool
    ) -> dict[str, Any]:
        job = session.get(Job, row.job_id)
        effective_status = (
            job.state
            if job is not None
            and (
                job.state in {"pending", "queued", "running"}
                or row.processed_at is None
            )
            else row.status
        )
        sources = list(
            session.scalars(
                select(CandidateSource).where(CandidateSource.search_run_id == row.id)
            )
        )
        first_count = 0
        for source in sources:
            first_run = session.scalar(
                select(SearchRun.id)
                .join(CandidateSource, CandidateSource.search_run_id == SearchRun.id)
                .where(CandidateSource.candidate_id == source.candidate_id)
                .order_by(SearchRun.created_at, SearchRun.id)
                .limit(1)
            )
            first_count += int(first_run == row.id)
        result: dict[str, Any] = {
            "id": row.id,
            "job_id": row.job_id,
            "brief_id": row.brief_id,
            "created_at": row.created_at,
            "keywords": row.keywords,
            "location": row.location,
            "network": row.network or [],
            "current_company": row.current_company,
            "status": effective_status,
            "reference_count": row.reference_count,
            "person_reference_count": row.person_reference_count,
            "new_candidate_count": first_count,
            "existing_candidate_count": max(0, len(sources) - first_count),
        }
        if detail:
            refs = list(
                session.scalars(
                    select(CandidateReference)
                    .where(CandidateReference.search_run_id == row.id)
                    .order_by(CandidateReference.position)
                )
            )
            errors = list(
                session.scalars(
                    select(SectionError)
                    .where(SectionError.search_run_id == row.id)
                    .order_by(SectionError.id)
                )
            )
            visible_errors = [
                {
                    "section_name": error.section_name,
                    "error_type": error.error_type,
                    "error_message": error.error_message,
                    "extra": error.extra or {},
                }
                for error in errors
            ]
            if not visible_errors and job is not None and job.error:
                visible_errors.append(
                    {
                        "section_name": "search_results",
                        "error_type": str(job.error).casefold(),
                        "error_message": _safe_job_message(job.error),
                        "extra": {},
                    }
                )
            raw_text = None
            if row.raw_response:
                try:
                    payload = MCPResponseEnvelope.model_validate(
                        row.raw_response
                    ).result_payload()
                    sections = payload.get("sections")
                    if isinstance(sections, dict) and isinstance(
                        sections.get("search_results"), str
                    ):
                        raw_text = sections["search_results"]
                except (ValueError, ValidationError):
                    pass
            result.update(
                {
                    "result_url": row.result_url,
                    "raw_text": raw_text,
                    "reference_kind_counts": dict(Counter(ref.kind for ref in refs)),
                    "references": [
                        {
                            "kind": ref.kind,
                            "url": ref.url or None,
                            "text": ref.text,
                            "context": ref.context,
                            "value": ref.value,
                            "position": ref.position,
                            **ref.extra,
                        }
                        for ref in refs
                    ],
                    "errors": visible_errors,
                }
            )
        return result

    def list_candidates(self, session_id: str) -> list[dict[str, Any]]:
        with self.database.sessions() as session:
            candidates = list(
                session.scalars(
                    select(Candidate)
                    .where(Candidate.session_id == session_id)
                    .order_by(Candidate.first_seen_at, Candidate.dedupe_key)
                )
            )
            output: list[dict[str, Any]] = []
            for candidate in candidates:
                source_rows = session.execute(
                    select(CandidateSource, SearchRun, CandidateReference)
                    .join(SearchRun, SearchRun.id == CandidateSource.search_run_id)
                    .join(
                        CandidateReference,
                        CandidateReference.id == CandidateSource.candidate_ref_id,
                    )
                    .where(CandidateSource.candidate_id == candidate.id)
                    .order_by(SearchRun.created_at, SearchRun.id)
                ).all()
                output.append(
                    {
                        "id": candidate.id,
                        "username": candidate.username,
                        "profile_url": candidate.profile_url,
                        "display_name": candidate.display_name,
                        "stage": "discovered",
                        "retrieval_status": candidate.retrieval_status,
                        "profile_urn": candidate.profile_urn,
                        "profile_urn_is_scored": False,
                        "source_count": len(source_rows),
                        "sources": [
                            {
                                "search_run_id": run.id,
                                "created_at": run.created_at,
                                "keywords": run.keywords,
                                "location": run.location,
                                "network_filter": run.network or [],
                                "current_company": run.current_company,
                                "reference_position": ref.position,
                                "reference_text": ref.text,
                                "reference_context": ref.context,
                                "notice": (
                                    "LinkedIn search-result context, not verified "
                                    "profile data."
                                ),
                            }
                            for _source, run, ref in source_rows
                        ],
                    }
                )
            return output
