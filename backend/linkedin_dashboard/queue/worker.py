from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError, OperationalError

from linkedin_dashboard.correlation import current_correlation_id
from linkedin_dashboard.db.models import (
    Candidate,
    DashboardSession,
    Job,
    JobAttempt,
    ProfileFetch,
    QueueControl,
)
from linkedin_dashboard.db.session import Database
from linkedin_dashboard.mcp.client import MCPClient
from linkedin_dashboard.mcp.envelope import MCPResponseEnvelope
from linkedin_dashboard.mcp.errors import ErrorClass, MCPClientError, classify
from linkedin_dashboard.queue.jobs import (
    JobKind,
    JobPayload,
    ListToolsPayload,
    PersonProfilePayload,
    max_attempts_for,
    missing_profile_sections,
    navigation_cost,
    persisted_payload,
    tool_arguments,
    unattempted_profile_navigation_count,
    validate_payload,
)

SYSTEM_SESSION_ID = "00000000-0000-0000-0000-000000000000"
SAFE_ERROR_MESSAGES = {
    ErrorClass.AUTH_REQUIRED: "LinkedIn authentication is required.",
    ErrorClass.BROWSER_BUSY: "The LinkedIn browser is currently in use.",
    ErrorClass.BROWSER_SETUP: "The LinkedIn browser is not ready.",
    ErrorClass.RATE_LIMIT: "LinkedIn rate-limited this request.",
    ErrorClass.INVALID_REFERENCE: "The LinkedIn reference is invalid.",
    ErrorClass.PROFILE_NOT_FOUND: "The LinkedIn profile was not found.",
    ErrorClass.TIMEOUT: "The MCP operation timed out.",
    ErrorClass.TRANSPORT: "The local MCP server is unreachable.",
    ErrorClass.UNKNOWN: "The MCP operation failed.",
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _after(seconds: float) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


@dataclass(frozen=True, slots=True)
class QueueEvent:
    event: str
    data: dict[str, Any]


class EventBroker:
    """Bounded fan-out; slow or disconnected browsers never block the worker."""

    def __init__(self, *, subscriber_capacity: int = 64) -> None:
        self._capacity = subscriber_capacity
        self._subscribers: set[asyncio.Queue[QueueEvent]] = set()

    def publish(self, event: QueueEvent) -> None:
        for subscriber in tuple(self._subscribers):
            if subscriber.full():
                try:
                    subscriber.get_nowait()
                except asyncio.QueueEmpty:  # pragma: no cover - defensive race
                    pass
            try:
                subscriber.put_nowait(event)
            except asyncio.QueueFull:  # pragma: no cover - bounded drop above
                pass

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[QueueEvent]]:
        subscriber: asyncio.Queue[QueueEvent] = asyncio.Queue(self._capacity)
        self._subscribers.add(subscriber)
        try:
            yield subscriber
        finally:
            self._subscribers.discard(subscriber)


RawCapture = Callable[[dict[str, Any] | None, dict[str, Any] | None], Awaitable[None]]
ProgressReporter = Callable[[float, float | None], Awaitable[None]]


class JobExecutor(Protocol):
    async def execute(
        self,
        payload: JobPayload,
        capture_raw: RawCapture,
        report_progress: ProgressReporter,
    ) -> dict[str, Any]: ...


class JobResultProcessor(Protocol):
    """Project a committed raw result into domain tables without network I/O."""

    def reconcile(self) -> None: ...

    def process_result(
        self, job_id: str, kind: JobKind, result: dict[str, Any]
    ) -> None: ...

    def process_failure(
        self, job_id: str, kind: JobKind, error_class: ErrorClass
    ) -> None: ...


class MCPReadExecutor:
    """The sole production dispatch table; messaging is intentionally absent."""

    def __init__(self, client: MCPClient) -> None:
        self._client = client

    async def execute(
        self,
        payload: JobPayload,
        capture_raw: RawCapture,
        report_progress: ProgressReporter,
    ) -> dict[str, Any]:
        if isinstance(payload, ListToolsPayload):
            del report_progress
            tools = await self._client.list_tools()
            raw = {"tools": [tool.model_dump(mode="json") for tool in tools]}
            await capture_raw(raw, None)
            return raw

        name, arguments = tool_arguments(payload)
        response = await self._client.call_tool(
            name,
            arguments,
            raw_response_capture=lambda raw: capture_raw(raw, None),
            progress_capture=lambda progress, total, _message: report_progress(
                progress, total
            ),
        )
        # Parsing happens only after the committed write-ahead capture.
        result = response.result_payload()
        if response.is_error:
            raise _ResponseError(response)
        return result


class _ResponseError(RuntimeError):
    def __init__(self, response: MCPResponseEnvelope) -> None:
        super().__init__("MCP tool returned an error envelope")
        self.response = response


class _ProjectionError(RuntimeError):
    """A local parser failed after the external response was committed."""


class _StaleClaim(RuntimeError):
    """A detached or superseded worker no longer owns durable state."""


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    id: str
    session_id: str
    kind: JobKind
    payload: JobPayload
    attempt_id: str
    attempt_number: int
    correlation_id: str


class DurableJobQueue:
    """A database-claimed, one-slot queue for allowlisted read operations."""

    def __init__(
        self,
        database: Database,
        executor: JobExecutor,
        *,
        inter_call_delay_seconds: float = 3.0,
        busy_retry_seconds: float = 30.0,
        timeout_retry_seconds: float = 0.0,
        rate_limit_cooldowns_seconds: tuple[float, ...] = (300.0, 900.0, 2700.0),
        shutdown_grace_seconds: float = 5.0,
        result_processor: JobResultProcessor | None = None,
    ) -> None:
        self.database = database
        self.executor = executor
        self.events = EventBroker()
        self.inter_call_delay_seconds = max(0.0, inter_call_delay_seconds)
        self.busy_retry_seconds = max(0.0, busy_retry_seconds)
        self.timeout_retry_seconds = max(0.0, timeout_retry_seconds)
        self.rate_limit_cooldowns_seconds = rate_limit_cooldowns_seconds
        self.shutdown_grace_seconds = shutdown_grace_seconds
        self.result_processor = result_processor
        self._wake = asyncio.Event()
        self._changed = asyncio.Condition()
        self._worker: asyncio.Task[None] | None = None
        self._start_lock = asyncio.Lock()
        self._accepting = False
        self._stopping = False
        self._owner_token: str | None = None
        self._worker_lock_fd: int | None = None

    async def start(self) -> None:
        async with self._start_lock:
            if self._worker is not None and not self._worker.done():
                return
            descriptor = self.database.acquire_worker_lock()
            owner_token = str(uuid4())
            try:
                self.database.initialize()
                self._prepare_startup(owner_token)
                if self.result_processor is not None:
                    self.result_processor.reconcile()
                self._worker_lock_fd = descriptor
                self._owner_token = owner_token
                self._stopping = False
                self._accepting = True
                self._worker = asyncio.create_task(
                    self._worker_loop(), name="linkedin-dashboard-job-worker"
                )
                self._wake.set()
            except BaseException:
                self.database.release_worker_lock(descriptor)
                raise

    async def stop(self) -> None:
        async with self._start_lock:
            if self._worker is None:
                self._accepting = False
                return
            worker = self._worker
            self._accepting = False
            self._stopping = True
            self._wake.set()
            done, _ = await asyncio.wait({worker}, timeout=self.shutdown_grace_seconds)
            if done:
                try:
                    worker.result()
                except BaseException:
                    pass
                self._clear_owner()
                self._release_worker_lock()
            else:
                worker.cancel()
                self._interrupt_owned()
                self._clear_owner()
                # Retain the OS lock until a cancellation-suppressing executor
                # actually exits. This keeps the hard shutdown bound without
                # allowing a replacement worker to overlap the detached call.
                worker.add_done_callback(self._detached_worker_finished)
            self._worker = None

    def _prepare_startup(self, owner_token: str) -> None:
        now = utc_now()
        with self.database.sessions.begin() as session:
            system = session.get(DashboardSession, SYSTEM_SESSION_ID)
            if system is None:
                session.add(
                    DashboardSession(
                        id=SYSTEM_SESSION_ID,
                        created_at=now,
                        label="System MCP status",
                        purge_after=(
                            datetime.now(UTC) + timedelta(days=3650)
                        ).isoformat(),
                        nav_budget=0,
                        nav_used=0,
                        send_enabled=False,
                    )
                )
            control = session.get(QueueControl, 1)
            if control is None:
                session.add(
                    QueueControl(
                        id=1,
                        state="active",
                        pause_reason=None,
                        resume_at=None,
                        rate_limit_count=0,
                        operator_resume_required=False,
                        last_mcp_finished_at=None,
                        owner_token=owner_token,
                        updated_at=now,
                    )
                )
            else:
                control.owner_token = owner_token
            running_ids = list(
                session.scalars(select(Job.id).where(Job.state == "running"))
            )
            if running_ids:
                session.execute(
                    update(Job)
                    .where(Job.id.in_(running_ids), Job.state == "running")
                    .values(state="interrupted", finished_at=now, claim_token=None)
                )
                session.execute(
                    update(JobAttempt)
                    .where(
                        JobAttempt.job_id.in_(running_ids),
                        JobAttempt.outcome == "running",
                    )
                    .values(outcome="interrupted", finished_at=now)
                )

    def _clear_owner(self) -> None:
        owner_token = self._owner_token
        if owner_token is None:
            return
        try:
            with self.database.sessions.begin() as session:
                session.execute(
                    update(QueueControl)
                    .where(
                        QueueControl.id == 1,
                        QueueControl.owner_token == owner_token,
                    )
                    .values(owner_token=None, updated_at=utc_now())
                )
        except Exception:
            pass

    def _interrupt_owned(self) -> None:
        owner_token = self._owner_token
        if owner_token is None:
            return
        now = utc_now()
        try:
            with self.database.sessions.begin() as session:
                owned_jobs = list(
                    session.scalars(
                        select(Job.id).where(
                            Job.state == "running", Job.claim_token == owner_token
                        )
                    )
                )
                if owned_jobs:
                    session.execute(
                        update(Job)
                        .where(
                            Job.id.in_(owned_jobs),
                            Job.state == "running",
                            Job.claim_token == owner_token,
                        )
                        .values(
                            state="interrupted",
                            finished_at=now,
                            claim_token=None,
                        )
                    )
                    session.execute(
                        update(JobAttempt)
                        .where(
                            JobAttempt.job_id.in_(owned_jobs),
                            JobAttempt.outcome == "running",
                            JobAttempt.worker_token == owner_token,
                        )
                        .values(outcome="interrupted", finished_at=now)
                    )
        except Exception:
            pass

    def _release_worker_lock(self) -> None:
        descriptor = self._worker_lock_fd
        self._worker_lock_fd = None
        self._owner_token = None
        if descriptor is not None:
            self.database.release_worker_lock(descriptor)

    def _detached_worker_finished(self, task: asyncio.Task[None]) -> None:
        if not task.cancelled():
            try:
                task.exception()
            except BaseException:
                pass
        self._release_worker_lock()

    async def enqueue(
        self,
        session_id: str,
        kind: JobKind | str,
        payload: dict[str, Any],
        *,
        correlation_id: str | None = None,
        related_factory: Callable[[Any, Job], None] | None = None,
    ) -> str:
        if not self._accepting:
            raise RuntimeError("queue is not accepting jobs")
        validated = validate_payload(kind, payload)
        if isinstance(validated, PersonProfilePayload) and validated.parent_job_id:
            raise ValueError(
                "profile continuations can only be generated by the worker"
            )
        normalized_kind = JobKind(kind)
        job = Job(
            id=str(uuid4()),
            session_id=session_id,
            kind=normalized_kind.value,
            payload=persisted_payload(validated),
            state="queued",
            attempts=0,
            max_attempts=max_attempts_for(normalized_kind),
            queued_at=utc_now(),
            started_at=None,
            finished_at=None,
            error=None,
            correlation_id=correlation_id or current_correlation_id(),
            claim_token=None,
        )
        with self.database.sessions.begin() as session:
            if session.get(DashboardSession, session_id) is None:
                raise LookupError("session does not exist")
            session.add(job)
            if related_factory is not None:
                # SQLAlchemy cannot infer ordering from scalar FK ids without
                # relationships; materialize the parent while retaining this
                # transaction's atomicity.
                session.flush()
                related_factory(session, job)
        self.events.publish(self._job_event(job.id, "queued"))
        self._publish_snapshot()
        self._wake.set()
        await self._notify_changed()
        return job.id

    async def cancel(self, job_id: str) -> bool:
        now = utc_now()
        kind: JobKind | None = None
        with self.database.sessions.begin() as session:
            job = session.get(Job, job_id)
            if job is not None:
                try:
                    kind = JobKind(job.kind)
                except ValueError:
                    kind = None
            result = session.execute(
                update(Job)
                .where(Job.id == job_id, Job.state == "queued")
                .values(state="cancelled", finished_at=now)
            )
            cancelled = isinstance(result, CursorResult) and result.rowcount == 1
            if cancelled and job is not None:
                self._terminalize_unstarted_profile(session, job, now)
        if cancelled:
            if self.result_processor is not None and kind is not None:
                self.result_processor.process_failure(job_id, kind, ErrorClass.UNKNOWN)
            self.events.publish(self._job_event(job_id, "cancelled"))
            self._publish_snapshot()
            self._wake.set()
            await self._notify_changed()
        return cancelled

    async def resume(self) -> None:
        resumed_ids: list[str] = []
        with self.database.sessions.begin() as session:
            if (
                session.scalar(
                    select(QueueControl.owner_token).where(QueueControl.id == 1)
                )
                != self._owner_token
            ):
                raise RuntimeError("queue is not the active owner")
            resumed_ids = list(
                session.scalars(select(Job.id).where(Job.state == "pending"))
            )
            session.execute(
                update(Job)
                .where(Job.id.in_(resumed_ids), Job.state == "pending")
                .values(state="queued")
            )
            control_result = session.execute(
                update(QueueControl)
                .where(
                    QueueControl.id == 1,
                    QueueControl.owner_token == self._owner_token,
                )
                .values(
                    state="active",
                    pause_reason=None,
                    resume_at=None,
                    operator_resume_required=False,
                    updated_at=utc_now(),
                )
            )
            if (
                not isinstance(control_result, CursorResult)
                or control_result.rowcount != 1
            ):
                raise RuntimeError("queue resume lost ownership")
        self.events.publish(
            QueueEvent(
                "queue",
                {"state": "active", "pause_reason": None, "resume_at": None},
            )
        )
        for job_id in resumed_ids:
            self.events.publish(self._job_event(job_id, "queued"))
        self._publish_snapshot()
        self._wake.set()
        await self._notify_changed()

    async def wait_for_terminal(self, job_id: str, wait_seconds: float = 250.0) -> Job:
        async with asyncio.timeout(wait_seconds):
            while True:
                with self.database.sessions() as session:
                    job = session.get(Job, job_id)
                    if job is None:
                        raise LookupError("job does not exist")
                    if job.state in {
                        "done",
                        "failed",
                        "interrupted",
                        "cancelled",
                    }:
                        session.expunge(job)
                        return job
                async with self._changed:
                    try:
                        async with asyncio.timeout(0.1):
                            await self._changed.wait()
                    except TimeoutError:
                        # Another process has its own condition variable; the
                        # durable row remains the cross-process notification.
                        pass

    async def probe_status(self, correlation_id: str | None = None) -> Job:
        job_id = await self.enqueue(
            SYSTEM_SESSION_ID,
            JobKind.LIST_TOOLS,
            {},
            correlation_id=correlation_id,
        )
        return await self.wait_for_terminal(job_id)

    def list_jobs(self, *, limit: int = 100) -> list[Job]:
        with self.database.sessions() as session:
            rows = list(
                session.scalars(
                    select(Job)
                    .order_by(Job.queued_at.desc(), Job.id.desc())
                    .limit(limit)
                )
            )
            for row in rows:
                session.expunge(row)
            return rows

    def attempts_for(self, job_id: str) -> list[JobAttempt]:
        with self.database.sessions() as session:
            rows = list(
                session.scalars(
                    select(JobAttempt)
                    .where(JobAttempt.job_id == job_id)
                    .order_by(JobAttempt.attempt_number)
                )
            )
            for row in rows:
                session.expunge(row)
            return rows

    def snapshot(self) -> dict[str, Any]:
        with self.database.sessions() as session:
            control = session.get(QueueControl, 1)
            counts = {
                str(state): int(count)
                for state, count in session.execute(
                    select(Job.state, func.count(Job.id)).group_by(Job.state)
                ).all()
            }
            active_jobs = list(
                session.scalars(
                    select(Job)
                    .where(Job.state.in_(("pending", "queued", "running")))
                    .order_by(Job.queued_at, Job.id)
                )
            )
            depth = len(active_jobs)
            waiting_position = 0
            jobs: list[dict[str, Any]] = []
            for job in active_jobs:
                position = None
                if job.state in {"pending", "queued"}:
                    waiting_position += 1
                    position = waiting_position
                jobs.append(self._job_data(job, position=position, depth=depth))
            return {
                "state": control.state if control else "active",
                "pause_reason": control.pause_reason if control else None,
                "resume_at": control.resume_at if control else None,
                "counts": counts,
                "jobs": jobs,
            }

    async def _worker_loop(self) -> None:
        while True:
            if self._stopping:
                return
            self._wake.clear()
            claimed, next_delay = self._claim_next()
            if claimed is None:
                # Claiming can terminalize malformed or over-budget rows even
                # when there is no executable job to announce. Reconcile all
                # subscribers with that durable state before waiting again.
                self._publish_snapshot()
                await self._notify_changed()
                if self._stopping:
                    return
                try:
                    if next_delay is None:
                        await self._wake.wait()
                    else:
                        await asyncio.wait_for(self._wake.wait(), timeout=next_delay)
                except TimeoutError:
                    self._wake.set()
                continue
            self.events.publish(self._job_event(claimed.id, "running"))
            self._publish_snapshot()
            try:
                await self._run_claimed(claimed)
            except asyncio.CancelledError:
                raise
            except BaseException:
                # A poison job is terminalized by _run_claimed; never kill the loop.
                await self._recover_failed_claim(claimed)

    async def _recover_failed_claim(self, job: ClaimedJob) -> None:
        """Keep transient SQLite contention from stranding a claimed job."""
        while not self._stopping:
            try:
                await self._fail_if_running(job, ErrorClass.UNKNOWN)
                return
            except _StaleClaim:
                return
            except (IntegrityError, OperationalError):
                # This retries only the local terminal write, never the MCP call.
                await asyncio.sleep(0.05)

    def _claim_next(self) -> tuple[ClaimedJob | None, float | None]:
        try:
            claimed = self._claim_next_transaction()
            if claimed[0] is None and self.result_processor is not None:
                self.result_processor.reconcile()
            return claimed
        except (IntegrityError, OperationalError):
            # The database-wide partial unique index is the final concurrency
            # guard if two app instances accidentally start workers.
            return None, 0.1

    def _claim_next_transaction(self) -> tuple[ClaimedJob | None, float | None]:
        now = datetime.now(UTC)
        owner_token = self._owner_token
        if owner_token is None:
            return None, None
        with self.database.sessions.begin() as session:
            control = session.get(QueueControl, 1)
            if control is None or control.owner_token != owner_token:
                return None, None
            if (
                control is not None
                and control.state == "paused"
                and not control.operator_resume_required
                and control.resume_at is not None
                and datetime.fromisoformat(control.resume_at) <= now
            ):
                control.state = "active"
                control.pause_reason = None
                control.resume_at = None
                control.updated_at = utc_now()
            paused = control is not None and control.state == "paused"
            jobs = list(
                session.scalars(
                    select(Job)
                    .where(Job.state == "queued")
                    .order_by(Job.queued_at, Job.id)
                )
            )
            earliest_delay: float | None = None
            if (
                paused
                and control is not None
                and not control.operator_resume_required
                and control.resume_at is not None
            ):
                earliest_delay = max(
                    0.0,
                    (datetime.fromisoformat(control.resume_at) - now).total_seconds(),
                )
            for job in jobs:
                if paused and job.kind != JobKind.LIST_TOOLS.value:
                    continue
                last_attempt = session.scalar(
                    select(JobAttempt)
                    .where(JobAttempt.job_id == job.id)
                    .order_by(JobAttempt.attempt_number.desc())
                    .limit(1)
                )
                if last_attempt is not None and last_attempt.retry_at:
                    retry_at = datetime.fromisoformat(last_attempt.retry_at)
                    delay = (retry_at - now).total_seconds()
                    if delay > 0:
                        earliest_delay = (
                            delay
                            if earliest_delay is None
                            else min(earliest_delay, delay)
                        )
                        continue
                try:
                    payload = validate_payload(JobKind(job.kind), job.payload or {})
                except (ValueError, ValidationError):
                    terminal_at = utc_now()
                    job.state = "failed"
                    job.finished_at = terminal_at
                    job.error = ErrorClass.UNKNOWN.value
                    self._terminalize_unstarted_profile(session, job, terminal_at)
                    continue
                if job.attempts == 0:
                    try:
                        cost = self._navigation_cost(session, job, payload)
                    except ValueError:
                        terminal_at = utc_now()
                        job.state = "failed"
                        job.finished_at = terminal_at
                        job.error = ErrorClass.UNKNOWN.value
                        self._terminalize_unstarted_profile(session, job, terminal_at)
                        continue
                    budget = session.get(DashboardSession, job.session_id)
                    if budget is None or budget.nav_used + cost > budget.nav_budget:
                        terminal_at = utc_now()
                        job.state = "failed"
                        job.finished_at = terminal_at
                        job.error = "BUDGET_EXHAUSTED"
                        self._terminalize_unstarted_profile(session, job, terminal_at)
                        continue
                else:
                    cost = 0
                    budget = None
                result = session.execute(
                    update(Job)
                    .where(Job.id == job.id, Job.state == "queued")
                    .values(
                        state="running",
                        attempts=Job.attempts + 1,
                        started_at=utc_now(),
                        finished_at=None,
                        error=None,
                        claim_token=owner_token,
                    )
                )
                if not isinstance(result, CursorResult) or result.rowcount != 1:
                    continue
                if budget is not None:
                    budget.nav_used += cost
                session.flush()
                session.refresh(job)
                attempt = JobAttempt(
                    id=str(uuid4()),
                    job_id=job.id,
                    attempt_number=job.attempts,
                    worker_token=owner_token,
                    started_at=utc_now(),
                    response_received_at=None,
                    finished_at=None,
                    outcome="running",
                    raw_response=None,
                    raw_error=None,
                    error_class=None,
                    safe_error_message=None,
                    retry_at=None,
                )
                session.add(attempt)
                session.flush()
                return (
                    ClaimedJob(
                        id=job.id,
                        session_id=job.session_id,
                        kind=JobKind(job.kind),
                        payload=payload,
                        attempt_id=attempt.id,
                        attempt_number=job.attempts,
                        correlation_id=job.correlation_id,
                    ),
                    None,
                )
            return None, earliest_delay

    @staticmethod
    def _terminalize_unstarted_profile(session: Any, job: Job, now: str) -> None:
        if job.kind != JobKind.GET_PERSON_PROFILE.value:
            return
        fetch = session.scalar(
            select(ProfileFetch).where(ProfileFetch.job_id == job.id)
        )
        if fetch is None or fetch.processed_at is not None:
            return
        fetch.outcome = "error"
        fetch.finished_at = now
        fetch.duration_ms = 0
        fetch.processed_at = now
        candidate = session.get(Candidate, fetch.candidate_id)
        if candidate is not None:
            candidate.retrieval_status = "failed"

    def _navigation_cost(self, session: Any, job: Job, payload: JobPayload) -> int:
        if not isinstance(payload, PersonProfilePayload) or not payload.parent_job_id:
            return navigation_cost(payload)
        parent = session.get(Job, payload.parent_job_id)
        if (
            parent is None
            or parent.session_id != job.session_id
            or parent.kind != JobKind.GET_PERSON_PROFILE.value
            or parent.state != "done"
            or parent.error != ErrorClass.RATE_LIMIT.value
        ):
            raise ValueError("invalid continuation parent")
        parent_attempt = session.scalar(
            select(JobAttempt)
            .where(JobAttempt.job_id == parent.id, JobAttempt.outcome == "ok")
            .order_by(JobAttempt.attempt_number.desc())
            .limit(1)
        )
        if parent_attempt is None or not isinstance(parent_attempt.raw_response, dict):
            raise ValueError("continuation parent has no durable response")
        try:
            envelope = MCPResponseEnvelope.model_validate(parent_attempt.raw_response)
            parent_payload = validate_payload(
                JobKind.GET_PERSON_PROFILE, parent.payload or {}
            )
            expected = missing_profile_sections(
                parent_payload, envelope.result_payload()
            )
        except (ValueError, ValidationError) as error:
            raise ValueError("continuation parent cannot be verified") from error
        if payload.sections != expected:
            raise ValueError("continuation does not match the missing suffix")
        return navigation_cost(payload)

    async def _run_claimed(self, job: ClaimedJob) -> None:
        try:
            await self._politeness_delay(job)

            async def capture_raw(
                response: dict[str, Any] | None, error: dict[str, Any] | None
            ) -> None:
                with self.database.sessions.begin() as session:
                    self._require_fence(session, job)
                    values: dict[str, Any] = {}
                    if response is not None:
                        values["raw_response"] = response
                        values["response_received_at"] = utc_now()
                    if error is not None:
                        values["raw_error"] = error
                    result = session.execute(
                        update(JobAttempt)
                        .where(
                            JobAttempt.id == job.attempt_id,
                            JobAttempt.outcome == "running",
                            JobAttempt.worker_token == self._owner_token,
                        )
                        .values(**values)
                    )
                    if not isinstance(result, CursorResult) or result.rowcount != 1:
                        raise _StaleClaim("job attempt is no longer writable")

            async def report_progress(progress: float, total: float | None) -> None:
                percent = None
                if total is not None and total > 0:
                    percent = max(0.0, min(100.0, progress / total * 100.0))
                self.events.publish(
                    QueueEvent(
                        "progress",
                        {
                            "id": job.id,
                            "state": "running",
                            "progress": progress,
                            "total": total,
                            "percent": percent,
                            "correlation_id": job.correlation_id,
                        },
                    )
                )

            result = await self.executor.execute(
                job.payload, capture_raw, report_progress
            )
            error_class = classify(result)
            if self.result_processor is not None:
                try:
                    self.result_processor.process_result(job.id, job.kind, result)
                except Exception as error:
                    # This must never turn into a second external call. The raw
                    # attempt remains restart-reconcilable by the local parser.
                    if error_class is ErrorClass.RATE_LIMIT:
                        await self._complete_rate_limited(job, result)
                        return
                    raise _ProjectionError from error
            if error_class is ErrorClass.RATE_LIMIT:
                await self._complete_rate_limited(job, result)
            else:
                await self._complete(job)
        except asyncio.CancelledError:
            await self._interrupt(job)
            raise
        except _StaleClaim:
            return
        except BaseException as error:
            await self._record_error_if_missing(job, error)
            error_class = (
                error.details.error_class
                if isinstance(error, MCPClientError)
                else (
                    classify(error.response)
                    if isinstance(error, _ResponseError)
                    else classify(error)
                )
            )
            retryable = error_class in {
                ErrorClass.BROWSER_BUSY,
                ErrorClass.TIMEOUT,
            } and job.attempt_number < max_attempts_for(job.kind)
            if (
                self.result_processor is not None
                and not retryable
                and not isinstance(error, _ProjectionError)
            ):
                if isinstance(error, _ResponseError):
                    try:
                        self.result_processor.process_result(
                            job.id, job.kind, error.response.result_payload()
                        )
                    except Exception:
                        # The raw error envelope is still durable and will be
                        # projected locally on startup; preserve its tool state.
                        pass
                else:
                    self.result_processor.process_failure(job.id, job.kind, error_class)
            if (
                isinstance(error, _ResponseError)
                and error_class is ErrorClass.RATE_LIMIT
            ):
                await self._complete_rate_limited(job, error.response.result_payload())
                return
            await self._handle_failure(job, error_class)

    def _require_fence(self, session: Any, job: ClaimedJob) -> None:
        owner = session.scalar(
            select(QueueControl.owner_token).where(QueueControl.id == 1)
        )
        if owner != self._owner_token:
            raise _StaleClaim("queue ownership changed")
        live_claim = session.scalar(
            select(Job.id).where(
                Job.id == job.id,
                Job.state == "running",
                Job.claim_token == self._owner_token,
            )
        )
        if live_claim is None:
            raise _StaleClaim("job claim changed")

    async def _record_error_if_missing(
        self, job: ClaimedJob, error: BaseException
    ) -> None:
        error_class = (
            error.details.error_class
            if isinstance(error, MCPClientError)
            else classify(error)
        )
        raw_error = {"error_class": error_class.value, "type": type(error).__name__}
        if (
            isinstance(error, MCPClientError)
            and error.details.partial_payload is not None
        ):
            raw_error["partial_payload"] = error.details.partial_payload
        with self.database.sessions.begin() as session:
            try:
                self._require_fence(session, job)
            except _StaleClaim:
                return
            attempt = session.get(JobAttempt, job.attempt_id)
            if attempt is not None and attempt.raw_error is None:
                result = session.execute(
                    update(JobAttempt)
                    .where(
                        JobAttempt.id == job.attempt_id,
                        JobAttempt.outcome == "running",
                        JobAttempt.worker_token == self._owner_token,
                    )
                    .values(raw_error=raw_error)
                )
                if not isinstance(result, CursorResult) or result.rowcount != 1:
                    raise _StaleClaim("job error capture lost its fence")

    async def _complete(self, job: ClaimedJob) -> None:
        now = utc_now()
        with self.database.sessions.begin() as session:
            self._require_fence(session, job)
            attempt_result = session.execute(
                update(JobAttempt)
                .where(
                    JobAttempt.id == job.attempt_id,
                    JobAttempt.outcome == "running",
                    JobAttempt.worker_token == self._owner_token,
                )
                .values(outcome="ok", finished_at=now)
            )
            job_result = session.execute(
                update(Job)
                .where(
                    Job.id == job.id,
                    Job.state == "running",
                    Job.claim_token == self._owner_token,
                )
                .values(
                    state="done",
                    finished_at=now,
                    error=None,
                    claim_token=None,
                )
            )
            if (
                not isinstance(attempt_result, CursorResult)
                or attempt_result.rowcount != 1
                or not isinstance(job_result, CursorResult)
                or job_result.rowcount != 1
            ):
                raise _StaleClaim("job completion lost its fence")
            control = session.get(QueueControl, 1)
            if control is not None and control.owner_token == self._owner_token:
                control.last_mcp_finished_at = now
                if job.kind is JobKind.LIST_TOOLS and control.pause_reason in {
                    ErrorClass.TRANSPORT.value,
                    ErrorClass.BROWSER_SETUP.value,
                }:
                    control.state = "active"
                    control.pause_reason = None
                    control.resume_at = None
                    control.operator_resume_required = False
                    control.updated_at = now
        await self._terminal_event(job, "done", None)

    async def _complete_rate_limited(
        self, job: ClaimedJob, result: dict[str, Any]
    ) -> None:
        now = utc_now()
        missing = missing_profile_sections(job.payload, result)
        followup_id: str | None = None
        with self.database.sessions.begin() as session:
            self._require_fence(session, job)
            control = session.get(QueueControl, 1)
            if control is None or control.owner_token != self._owner_token:
                raise _StaleClaim("rate-limited job lost its owner")
            attempt_result = session.execute(
                update(JobAttempt)
                .where(
                    JobAttempt.id == job.attempt_id,
                    JobAttempt.outcome == "running",
                    JobAttempt.worker_token == self._owner_token,
                )
                .values(
                    outcome="ok",
                    error_class=ErrorClass.RATE_LIMIT.value,
                    finished_at=now,
                )
            )
            job_result = session.execute(
                update(Job)
                .where(
                    Job.id == job.id,
                    Job.state == "running",
                    Job.claim_token == self._owner_token,
                )
                .values(
                    state="done",
                    finished_at=now,
                    error=ErrorClass.RATE_LIMIT.value,
                    claim_token=None,
                )
            )
            if (
                not isinstance(attempt_result, CursorResult)
                or attempt_result.rowcount != 1
                or not isinstance(job_result, CursorResult)
                or job_result.rowcount != 1
            ):
                raise _StaleClaim("rate-limit completion lost its fence")
            next_count = min(control.rate_limit_count + 1, 3)
            cooldown = self.rate_limit_cooldowns_seconds[
                min(next_count - 1, len(self.rate_limit_cooldowns_seconds) - 1)
            ]
            control.state = "paused"
            control.pause_reason = ErrorClass.RATE_LIMIT.value
            control.resume_at = _after(cooldown)
            control.rate_limit_count = next_count
            control.operator_resume_required = True
            control.last_mcp_finished_at = now
            control.updated_at = now
            skipped_navigations = unattempted_profile_navigation_count(
                job.payload, result
            )
            dashboard_session = session.get(DashboardSession, job.session_id)
            if dashboard_session is not None and skipped_navigations:
                dashboard_session.nav_used = max(
                    0, dashboard_session.nav_used - skipped_navigations
                )
            if missing and isinstance(job.payload, PersonProfilePayload):
                followup_payload = PersonProfilePayload(
                    linkedin_username=job.payload.linkedin_username,
                    sections=missing,
                    max_scrolls=job.payload.max_scrolls,
                    parent_job_id=job.id,
                )
                duplicate = session.scalar(
                    select(Job.id).where(
                        Job.kind == JobKind.GET_PERSON_PROFILE.value,
                        Job.payload == persisted_payload(followup_payload),
                        Job.state.in_(("pending", "queued", "running", "done")),
                    )
                )
                if duplicate is None:
                    followup_id = str(uuid4())
                    followup_job = Job(
                        id=followup_id,
                        session_id=job.session_id,
                        kind=JobKind.GET_PERSON_PROFILE.value,
                        payload=persisted_payload(followup_payload),
                        state="pending",
                        attempts=0,
                        max_attempts=2,
                        queued_at=now,
                        started_at=None,
                        finished_at=None,
                        error=None,
                        correlation_id=job.correlation_id,
                        claim_token=None,
                    )
                    session.add(followup_job)
                    parent_fetch = session.scalar(
                        select(ProfileFetch).where(ProfileFetch.job_id == job.id)
                    )
                    if parent_fetch is not None:
                        child_fetch_id = str(uuid4())
                        session.add(
                            ProfileFetch(
                                id=child_fetch_id,
                                candidate_id=parent_fetch.candidate_id,
                                job_id=followup_id,
                                tool=JobKind.GET_PERSON_PROFILE.value,
                                requested_sections=["main_profile", *missing],
                                args={
                                    "linkedin_username": (
                                        job.payload.linkedin_username
                                    ),
                                    "sections": missing,
                                    **(
                                        {"max_scrolls": job.payload.max_scrolls}
                                        if job.payload.max_scrolls is not None
                                        else {}
                                    ),
                                },
                                started_at=now,
                                finished_at=None,
                                duration_ms=None,
                                outcome=None,
                                raw_response=None,
                                returned_url=None,
                                processed_at=None,
                                request_stage="resume",
                                parent_fetch_id=parent_fetch.id,
                                root_fetch_id=parent_fetch.root_fetch_id,
                            )
                        )
        if followup_id is not None:
            self.events.publish(self._job_event(followup_id, "pending"))
        self.events.publish(
            QueueEvent(
                "queue",
                {
                    "state": "paused",
                    "pause_reason": ErrorClass.RATE_LIMIT.value,
                    "resume_at": self.snapshot()["resume_at"],
                },
            )
        )
        await self._terminal_event(job, "done", ErrorClass.RATE_LIMIT)

    async def _handle_failure(self, job: ClaimedJob, error_class: ErrorClass) -> None:
        retry_delay: float | None = None
        if error_class is ErrorClass.BROWSER_BUSY:
            retry_delay = self.busy_retry_seconds
        elif error_class is ErrorClass.TIMEOUT:
            retry_delay = self.timeout_retry_seconds

        now = utc_now()
        target_state = "failed"
        with self.database.sessions.begin() as session:
            self._require_fence(session, job)
            current = session.get(Job, job.id)
            control = session.get(QueueControl, 1)
            if (
                current is None
                or control is None
                or control.owner_token != self._owner_token
            ):
                raise _StaleClaim("failed job lost its fence")
            can_retry = (
                retry_delay is not None and current.attempts < current.max_attempts
            )
            target_state = "queued" if can_retry else "failed"
            current_attempt = session.get(JobAttempt, job.attempt_id)
            refund_profile_navigation = (
                not can_retry
                and current.kind == JobKind.GET_PERSON_PROFILE.value
                and current_attempt is not None
                and current_attempt.response_received_at is None
            )
            attempt_result = session.execute(
                update(JobAttempt)
                .where(
                    JobAttempt.id == job.attempt_id,
                    JobAttempt.outcome == "running",
                    JobAttempt.worker_token == self._owner_token,
                )
                .values(
                    outcome="error",
                    error_class=error_class.value,
                    safe_error_message=SAFE_ERROR_MESSAGES[error_class],
                    retry_at=_after(retry_delay) if can_retry else None,
                    finished_at=now,
                )
            )
            job_result = session.execute(
                update(Job)
                .where(
                    Job.id == job.id,
                    Job.state == "running",
                    Job.claim_token == self._owner_token,
                )
                .values(
                    state=target_state,
                    finished_at=None if can_retry else now,
                    error=error_class.value,
                    claim_token=None,
                )
            )
            if (
                not isinstance(attempt_result, CursorResult)
                or attempt_result.rowcount != 1
                or not isinstance(job_result, CursorResult)
                or job_result.rowcount != 1
            ):
                raise _StaleClaim("failure transition lost its fence")
            if refund_profile_navigation:
                dashboard_session = session.get(DashboardSession, current.session_id)
                if dashboard_session is not None:
                    dashboard_session.nav_used = max(
                        0,
                        dashboard_session.nav_used - navigation_cost(job.payload),
                    )
                self._terminalize_unstarted_profile(session, current, now)
            if error_class in {
                ErrorClass.AUTH_REQUIRED,
                ErrorClass.BROWSER_SETUP,
                ErrorClass.TRANSPORT,
            }:
                control.state = "paused"
                control.pause_reason = error_class.value
                control.resume_at = (
                    _after(60.0) if error_class is ErrorClass.BROWSER_SETUP else None
                )
                control.operator_resume_required = (
                    error_class is not ErrorClass.BROWSER_SETUP
                )
                control.updated_at = now
            control.last_mcp_finished_at = now
        if target_state == "queued":
            self._wake.set()
            event = self._job_event(job.id, "queued")
            event.data["error_class"] = error_class.value
            event.data["message"] = SAFE_ERROR_MESSAGES[error_class]
            self.events.publish(event)
            self._publish_snapshot()
            await self._notify_changed()
        else:
            await self._terminal_event(job, "failed", error_class)

    async def _interrupt(self, job: ClaimedJob) -> None:
        now = utc_now()
        with self.database.sessions.begin() as session:
            try:
                self._require_fence(session, job)
            except _StaleClaim:
                return
            job_result = session.execute(
                update(Job)
                .where(
                    Job.id == job.id,
                    Job.state == "running",
                    Job.claim_token == self._owner_token,
                )
                .values(state="interrupted", finished_at=now, claim_token=None)
            )
            attempt_result = session.execute(
                update(JobAttempt)
                .where(
                    JobAttempt.id == job.attempt_id,
                    JobAttempt.outcome == "running",
                    JobAttempt.worker_token == self._owner_token,
                )
                .values(outcome="interrupted", finished_at=now)
            )
            if (
                not isinstance(attempt_result, CursorResult)
                or attempt_result.rowcount != 1
                or not isinstance(job_result, CursorResult)
                or job_result.rowcount != 1
            ):
                raise _StaleClaim("interrupt transition lost its fence")
        await self._terminal_event(job, "interrupted", None)

    async def _fail_if_running(self, job: ClaimedJob, error_class: ErrorClass) -> None:
        now = utc_now()
        with self.database.sessions.begin() as session:
            try:
                self._require_fence(session, job)
            except _StaleClaim:
                return
            job_result = session.execute(
                update(Job)
                .where(
                    Job.id == job.id,
                    Job.state == "running",
                    Job.claim_token == self._owner_token,
                )
                .values(
                    state="failed",
                    finished_at=now,
                    error=error_class.value,
                    claim_token=None,
                )
            )
            attempt_result = session.execute(
                update(JobAttempt)
                .where(
                    JobAttempt.id == job.attempt_id,
                    JobAttempt.outcome == "running",
                    JobAttempt.worker_token == self._owner_token,
                )
                .values(
                    outcome="error",
                    finished_at=now,
                    error_class=error_class.value,
                    safe_error_message=SAFE_ERROR_MESSAGES[error_class],
                )
            )
            if (
                not isinstance(attempt_result, CursorResult)
                or attempt_result.rowcount != 1
                or not isinstance(job_result, CursorResult)
                or job_result.rowcount != 1
            ):
                return
        await self._terminal_event(job, "failed", error_class)

    async def _politeness_delay(self, job: ClaimedJob) -> None:
        if self.inter_call_delay_seconds <= 0:
            return
        with self.database.sessions() as session:
            control = session.get(QueueControl, 1)
            previous = control.last_mcp_finished_at if control else None
        if previous is None:
            return
        elapsed = (datetime.now(UTC) - datetime.fromisoformat(previous)).total_seconds()
        remaining = self.inter_call_delay_seconds - elapsed
        if remaining > 0:
            await asyncio.sleep(remaining)

    async def _terminal_event(
        self, job: ClaimedJob, state: str, error_class: ErrorClass | None
    ) -> None:
        event = self._job_event(job.id, state)
        data = event.data
        if error_class is not None:
            data["error_class"] = error_class.value
            data["message"] = SAFE_ERROR_MESSAGES[error_class]
        self.events.publish(event)
        self._publish_snapshot()
        await self._notify_changed()

    async def _notify_changed(self) -> None:
        async with self._changed:
            self._changed.notify_all()

    def _job_event(self, job_id: str, state: str) -> QueueEvent:
        with self.database.sessions() as session:
            job = session.get(Job, job_id)
            if job is None:
                return QueueEvent("job", {"id": job_id, "state": state})
            data = self._safe_job_data(job)
            data["state"] = state
            return QueueEvent("job", data)

    def _safe_job_data(self, job: Job) -> dict[str, Any]:
        position, depth = self.queue_position(job.id)
        return self._job_data(job, position=position, depth=depth)

    @staticmethod
    def _job_data(job: Job, *, position: int | None, depth: int) -> dict[str, Any]:
        return {
            "id": job.id,
            "kind": job.kind,
            "state": job.state,
            "position": position,
            "depth": depth,
            "error_class": job.error,
            "correlation_id": job.correlation_id,
        }

    def _publish_snapshot(self) -> None:
        """Publish a canonical view after any active-queue transition."""
        self.events.publish(QueueEvent("snapshot", self.snapshot()))

    def queue_position(self, job_id: str) -> tuple[int | None, int]:
        with self.database.sessions() as session:
            job = session.get(Job, job_id)
            depth = int(
                session.scalar(
                    select(func.count(Job.id)).where(
                        Job.state.in_(("pending", "queued", "running"))
                    )
                )
                or 0
            )
            if job is None or job.state not in {"pending", "queued"}:
                return None, depth
            earlier = int(
                session.scalar(
                    select(func.count(Job.id)).where(
                        Job.state.in_(("pending", "queued")),
                        (Job.queued_at < job.queued_at)
                        | ((Job.queued_at == job.queued_at) & (Job.id < job.id)),
                    )
                )
                or 0
            )
            return earlier + 1, depth
