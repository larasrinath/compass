from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from linkedin_dashboard.db.models import (
    Candidate,
    DashboardSession,
    Job,
    JobAttempt,
    MessageDraft,
    QueueControl,
    SendAttempt,
)
from linkedin_dashboard.db.session import Database
from linkedin_dashboard.main import create_app
from linkedin_dashboard.queue.jobs import JobKind, JobPayload
from linkedin_dashboard.queue.worker import (
    DurableJobQueue,
    ProgressReporter,
    RawCapture,
)
from linkedin_dashboard.settings import Settings
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, OperationalError

NOW = "2026-09-03T12:00:00+00:00"


def add_session(database: Database, session_id: str = "session-queue") -> str:
    with database.sessions.begin() as session:
        session.add(
            DashboardSession(
                id=session_id,
                created_at=NOW,
                label="Queue tests",
                purge_after="2026-10-03T12:00:00+00:00",
                nav_budget=120,
                nav_used=0,
                send_enabled=False,
            )
        )
    return session_id


class RecordingExecutor:
    def __init__(self, responses: list[object] | None = None) -> None:
        self.responses = list(responses or [])
        self.active = 0
        self.max_active = 0
        self.calls: list[JobPayload] = []

    async def execute(
        self,
        payload: JobPayload,
        capture_raw: RawCapture,
        report_progress: ProgressReporter,
    ) -> dict[str, Any]:
        self.calls.append(payload)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await report_progress(1, 2)
            await asyncio.sleep(0.005)
            response = self.responses.pop(0) if self.responses else {"url": "ok"}
            if isinstance(response, BaseException):
                raise response
            assert isinstance(response, dict)
            await capture_raw(
                {
                    "content": [{"type": "text", "text": "stored"}],
                    "structuredContent": response,
                    "isError": False,
                },
                None,
            )
            return response
        finally:
            self.active -= 1


def new_database(path: Path) -> Database:
    database = Database(path)
    database.initialize()
    return database


@pytest.mark.asyncio
async def test_ten_jobs_execute_strictly_one_at_a_time(tmp_path) -> None:
    database = new_database(tmp_path / "sequential.db")
    executor = RecordingExecutor()
    queue = DurableJobQueue(database, executor, inter_call_delay_seconds=0)
    await queue.start()
    session_id = add_session(database)
    try:
        job_ids = await asyncio.gather(
            *(
                queue.enqueue(session_id, JobKind.SEARCH_PEOPLE, {"keywords": f"k{i}"})
                for i in range(10)
            )
        )
        await asyncio.gather(*(queue.wait_for_terminal(job_id) for job_id in job_ids))
        assert executor.max_active == 1
        assert len(executor.calls) == 10
        assert all(job.state == "done" for job in queue.list_jobs())
    finally:
        await queue.stop()
        database.dispose()


@pytest.mark.asyncio
async def test_raw_capture_commits_before_executor_parses(tmp_path) -> None:
    database = new_database(tmp_path / "write-before-parse.db")

    class InspectingExecutor:
        async def execute(
            self,
            payload: JobPayload,
            capture_raw: RawCapture,
            report_progress: ProgressReporter,
        ) -> dict[str, Any]:
            del payload, report_progress
            raw = {
                "content": [{"type": "text", "text": "private profile text"}],
                "structuredContent": {"url": "ok", "unexpected": True},
                "isError": False,
            }
            await capture_raw(raw, None)
            with database.sessions() as session:
                attempt = session.scalar(
                    select(JobAttempt).where(JobAttempt.outcome == "running")
                )
                assert attempt is not None
                assert attempt.raw_response == raw
                assert attempt.response_received_at is not None
            raise ValueError("parser rejected response")

    queue = DurableJobQueue(database, InspectingExecutor(), inter_call_delay_seconds=0)
    await queue.start()
    session_id = add_session(database)
    try:
        job_id = await queue.enqueue(
            session_id, JobKind.SEARCH_PEOPLE, {"keywords": "database"}
        )
        job = await queue.wait_for_terminal(job_id)
        attempt = queue.attempts_for(job_id)[0]
        assert job.state == "failed"
        assert attempt.raw_response is not None
        assert attempt.outcome == "error"
    finally:
        await queue.stop()
        database.dispose()


@pytest.mark.asyncio
async def test_startup_marks_orphaned_work_interrupted_without_replay(tmp_path) -> None:
    path = tmp_path / "restart.db"
    database = new_database(path)
    starter = DurableJobQueue(database, RecordingExecutor(), inter_call_delay_seconds=0)
    await starter.start()
    session_id = add_session(database)
    await starter.stop()
    with database.sessions.begin() as session:
        job = Job(
            id="orphan-job",
            session_id=session_id,
            kind=JobKind.SEARCH_PEOPLE.value,
            payload={"keywords": "orphan"},
            state="running",
            attempts=1,
            max_attempts=2,
            queued_at=NOW,
            started_at=NOW,
            finished_at=None,
            error=None,
            correlation_id="restart-test",
            claim_token="dead-worker",
        )
        session.add(job)
        session.add(
            JobAttempt(
                id="orphan-attempt",
                job_id=job.id,
                attempt_number=1,
                worker_token="dead-worker",
                started_at=NOW,
                response_received_at=NOW,
                finished_at=None,
                outcome="running",
                raw_response={"content": [], "structuredContent": {"url": "ok"}},
                raw_error=None,
                error_class=None,
                safe_error_message=None,
                retry_at=None,
            )
        )
    database.dispose()

    restarted = new_database(path)
    executor = RecordingExecutor()
    queue = DurableJobQueue(restarted, executor, inter_call_delay_seconds=0)
    try:
        await queue.start()
        await asyncio.sleep(0.02)
        with restarted.sessions() as session:
            orphan_job = session.get(Job, "orphan-job")
            orphan_attempt = session.get(JobAttempt, "orphan-attempt")
            assert orphan_job is not None and orphan_job.state == "interrupted"
            assert (
                orphan_attempt is not None and orphan_attempt.outcome == "interrupted"
            )
        assert executor.calls == []
    finally:
        await queue.stop()
        restarted.dispose()


@pytest.mark.asyncio
async def test_queued_cancel_is_cas_and_running_job_is_not_cancelled(tmp_path) -> None:
    database = new_database(tmp_path / "cancel.db")
    blocker = asyncio.Event()
    started = asyncio.Event()

    class BlockingExecutor(RecordingExecutor):
        async def execute(self, payload, capture_raw, report_progress):
            self.calls.append(payload)
            started.set()
            await blocker.wait()
            await capture_raw({"content": [], "structuredContent": {"url": "ok"}}, None)
            return {"url": "ok"}

    executor = BlockingExecutor()
    queue = DurableJobQueue(database, executor, inter_call_delay_seconds=0)
    await queue.start()
    session_id = add_session(database)
    try:
        running_id = await queue.enqueue(
            session_id, JobKind.SEARCH_PEOPLE, {"keywords": "running"}
        )
        await started.wait()
        queued_id = await queue.enqueue(
            session_id, JobKind.SEARCH_PEOPLE, {"keywords": "queued"}
        )
        assert await queue.cancel(running_id) is False
        assert await queue.cancel(queued_id) is True
        blocker.set()
        assert (await queue.wait_for_terminal(running_id)).state == "done"
        assert (await queue.wait_for_terminal(queued_id)).state == "cancelled"
        assert len(executor.calls) == 1
    finally:
        blocker.set()
        await queue.stop()
        database.dispose()


@pytest.mark.asyncio
async def test_rate_limit_holds_exact_suffix_until_operator_resume(tmp_path) -> None:
    database = new_database(tmp_path / "rate-limit.db")
    first = {
        "url": "https://www.linkedin.com/in/person/",
        "sections": {"main_profile": "profile"},
        "section_errors": {
            "experience": {"error_type": "rate_limit", "runtime": {"secret": 1}}
        },
    }
    second = {
        "url": "https://www.linkedin.com/in/person/",
        "sections": {"experience": "work", "skills": "python"},
    }
    executor = RecordingExecutor([first, second])
    queue = DurableJobQueue(
        database,
        executor,
        inter_call_delay_seconds=0,
        rate_limit_cooldowns_seconds=(0.01,),
    )
    await queue.start()
    session_id = add_session(database)
    try:
        job_id = await queue.enqueue(
            session_id,
            JobKind.GET_PERSON_PROFILE,
            {
                "linkedin_username": "person",
                "sections": ["main_profile", "experience", "skills"],
            },
        )
        assert (await queue.wait_for_terminal(job_id)).state == "done"
        await asyncio.sleep(0.03)
        assert len(executor.calls) == 1
        snapshot = queue.snapshot()
        assert snapshot["state"] == "paused"
        assert snapshot["pause_reason"] == "RATE_LIMIT"
        held = [job for job in queue.list_jobs() if job.state == "pending"]
        assert len(held) == 1
        assert held[0].payload == {
            "linkedin_username": "person",
            "sections": ["experience", "skills"],
            "parent_job_id": job_id,
        }
        async with queue.events.subscribe() as subscriber:
            await queue.resume()
            resumed_queue = await asyncio.wait_for(subscriber.get(), timeout=1)
            resumed_job = await asyncio.wait_for(subscriber.get(), timeout=1)
        assert resumed_queue.event == "queue"
        assert resumed_queue.data == {
            "state": "active",
            "pause_reason": None,
            "resume_at": None,
        }
        assert resumed_job.event == "job"
        assert resumed_job.data["id"] == held[0].id
        assert resumed_job.data["state"] == "queued"
        assert (await queue.wait_for_terminal(held[0].id)).state == "done"
        assert len(executor.calls) == 2
    finally:
        await queue.stop()
        database.dispose()


@pytest.mark.asyncio
async def test_timeout_retries_once_as_a_new_durable_attempt(tmp_path) -> None:
    database = new_database(tmp_path / "retry.db")
    executor = RecordingExecutor([TimeoutError(), {"url": "ok"}])
    queue = DurableJobQueue(
        database,
        executor,
        inter_call_delay_seconds=0,
        timeout_retry_seconds=0,
    )
    await queue.start()
    session_id = add_session(database)
    try:
        job_id = await queue.enqueue(
            session_id, JobKind.SEARCH_PEOPLE, {"keywords": "retry"}
        )
        job = await queue.wait_for_terminal(job_id)
        assert job.state == "done"
        assert job.attempts == 2
        assert [attempt.outcome for attempt in queue.attempts_for(job_id)] == [
            "error",
            "ok",
        ]
    finally:
        await queue.stop()
        database.dispose()


@pytest.mark.asyncio
async def test_budget_is_reserved_once_and_exhaustion_makes_no_call(tmp_path) -> None:
    database = new_database(tmp_path / "budget.db")
    executor = RecordingExecutor()
    queue = DurableJobQueue(database, executor, inter_call_delay_seconds=0)
    await queue.start()
    session_id = add_session(database)
    with database.sessions.begin() as session:
        dashboard_session = session.get(DashboardSession, session_id)
        assert dashboard_session is not None
        dashboard_session.nav_budget = 1
    try:
        first = await queue.enqueue(
            session_id, JobKind.SEARCH_PEOPLE, {"keywords": "within"}
        )
        second = await queue.enqueue(
            session_id, JobKind.SEARCH_PEOPLE, {"keywords": "over"}
        )
        assert (await queue.wait_for_terminal(first)).state == "done"
        rejected = await queue.wait_for_terminal(second)
        assert rejected.state == "failed"
        assert rejected.error == "BUDGET_EXHAUSTED"
        assert len(executor.calls) == 1
        with database.sessions() as session:
            dashboard_session = session.get(DashboardSession, session_id)
            assert dashboard_session is not None and dashboard_session.nav_used == 1
    finally:
        await queue.stop()
        database.dispose()


@pytest.mark.asyncio
async def test_two_workers_still_cannot_overlap_external_calls(tmp_path) -> None:
    database = new_database(tmp_path / "two-workers.db")
    started = asyncio.Event()
    release = asyncio.Event()

    class HeldExecutor(RecordingExecutor):
        async def execute(self, payload, capture_raw, report_progress):
            self.calls.append(payload)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await capture_raw(
                {"content": [], "structuredContent": {"url": "durable"}}, None
            )
            started.set()
            try:
                await release.wait()
                return {"url": "durable"}
            finally:
                self.active -= 1

    executor = HeldExecutor()
    first = DurableJobQueue(database, executor, inter_call_delay_seconds=0)
    second = DurableJobQueue(database, executor, inter_call_delay_seconds=0)
    await first.start()
    session_id = add_session(database)
    try:
        job_id = await first.enqueue(
            session_id, JobKind.SEARCH_PEOPLE, {"keywords": "live-owner"}
        )
        await started.wait()
        with database.sessions() as session:
            control = session.get(QueueControl, 1)
            assert control is not None
            first_owner = control.owner_token
        with pytest.raises(BlockingIOError, match="queue owner"):
            await second.start()
        with database.sessions() as session:
            running = session.get(Job, job_id)
            attempt = session.scalar(
                select(JobAttempt).where(JobAttempt.job_id == job_id)
            )
            control = session.get(QueueControl, 1)
            assert running is not None and running.state == "running"
            assert attempt is not None and attempt.raw_response is not None
            assert control is not None and control.owner_token == first_owner
        lock_path = database.path.with_name(f"{database.path.name}.queue.lock")
        assert lock_path.stat().st_mode & 0o777 == 0o600
        release.set()
        assert (await first.wait_for_terminal(job_id)).state == "done"
        assert executor.max_active == 1
        assert len(executor.calls) == 1
    finally:
        release.set()
        await first.stop()
        await second.stop()
        database.dispose()


def test_database_rejects_send_and_a_second_running_job(database) -> None:
    session_id = add_session(database)
    with pytest.raises(IntegrityError, match="job kind or attempt policy"):
        with database.sessions.begin() as session:
            session.add(
                Job(
                    id="send-job",
                    session_id=session_id,
                    kind="send_message",
                    payload={},
                    state="queued",
                    attempts=0,
                    max_attempts=1,
                    queued_at=NOW,
                    correlation_id="guard",
                )
            )

    with database.sessions.begin() as session:
        for suffix in ("one", "two"):
            session.add(
                Job(
                    id=f"job-{suffix}",
                    session_id=session_id,
                    kind=JobKind.SEARCH_PEOPLE.value,
                    payload={"keywords": suffix},
                    state="queued",
                    attempts=0,
                    max_attempts=2,
                    queued_at=NOW,
                    correlation_id="guard",
                )
            )
    with database.sessions.begin() as session:
        session.execute(
            update(Job)
            .where(Job.id == "job-one")
            .values(
                state="running",
                attempts=1,
                started_at=NOW,
                claim_token="worker-one",
            )
        )
    with pytest.raises(IntegrityError, match="one_running_job"):
        with database.sessions.begin() as session:
            session.execute(
                update(Job)
                .where(Job.id == "job-two")
                .values(
                    state="running",
                    attempts=1,
                    started_at=NOW,
                    claim_token="worker-two",
                )
            )


def test_queue_code_has_no_messaging_or_send_attempt_dependency() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "backend/linkedin_dashboard/queue/worker.py"
    ).read_text()
    assert "LinkedInMessagingTools" not in source
    assert "send_attempt" not in source.casefold()
    assert "send_message" not in source.casefold()


def test_queue_control_pause_survives_database_restart(tmp_path) -> None:
    path = tmp_path / "pause-restart.db"
    database = new_database(path)
    database.initialize()
    now = datetime.now(UTC).isoformat()
    with database.sessions.begin() as session:
        session.add(
            QueueControl(
                id=1,
                state="paused",
                pause_reason="RATE_LIMIT",
                resume_at=(datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
                rate_limit_count=1,
                operator_resume_required=True,
                last_mcp_finished_at=now,
                updated_at=now,
            )
        )
    database.dispose()
    restarted = new_database(path)
    try:
        with restarted.sessions() as session:
            control = session.get(QueueControl, 1)
            assert control is not None and control.state == "paused"
            assert control.operator_resume_required is True
    finally:
        restarted.dispose()


def test_status_and_job_apis_never_return_durable_raw_payload(tmp_path) -> None:
    private_path = str(Path.home() / ".linkedin-mcp/profile")

    class StatusExecutor:
        async def execute(self, payload, capture_raw, report_progress):
            del payload, report_progress
            raw = {
                "tools": [
                    {
                        "name": "search_people",
                        "runtime": {"cookie_path": private_path},
                    }
                ]
            }
            await capture_raw(raw, None)
            return raw

    app = create_app(
        Settings(
            db_path=tmp_path / "api.db",
            inter_call_delay_seconds=0,
        ),
        queue_executor=StatusExecutor(),
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        status = client.get("/api/mcp/status")
        jobs = client.get("/api/jobs")

    assert status.status_code == 200
    assert status.json()["reachable"] is True
    assert status.json()["tools"] == ["search_people"]
    assert jobs.status_code == 200
    for response in (status, jobs):
        assert private_path not in response.text
        assert ".linkedin-mcp" not in response.text
        assert "runtime" not in response.text
        assert "raw_response" not in response.text
        assert "payload" not in response.text


@pytest.mark.asyncio
async def test_queue_lifecycle_never_touches_ambiguous_send_attempt(tmp_path) -> None:
    database = new_database(tmp_path / "send-invariant.db")
    session_id = add_session(database)
    with database.sessions.begin() as session:
        session.add(
            Candidate(
                id="candidate-send-guard",
                session_id=session_id,
                username="guarded-person",
                profile_url="https://www.linkedin.com/in/guarded-person/",
                display_name="Guarded Person",
                profile_urn=None,
                first_seen_at=NOW,
                stage="discovered",
                retrieval_status="pending",
            )
        )
        session.add(
            MessageDraft(
                id="draft-send-guard",
                candidate_id="candidate-send-guard",
                version=1,
                body="Hello",
                body_sha256="a" * 64,
                char_count=5,
                generator="manual",
                grounding_status="pass",
                grounding_report={},
                created_at=NOW,
            )
        )
        session.add(
            SendAttempt(
                id="attempt-send-guard",
                candidate_id="candidate-send-guard",
                draft_id="draft-send-guard",
                idempotency_key="b" * 64,
                body_sha256="a" * 64,
                confirm_send=True,
                state="AMBIGUOUS",
                tool_status="send_unavailable",
                tool_sent=False,
                tool_recipient_selected=True,
                tool_url="https://www.linkedin.com/messaging/",
                raw_response={"status": "send_unavailable"},
                error_class=None,
                error_message=None,
                started_at=NOW,
                finished_at=NOW,
                resolution="unresolved",
                resolved_at=None,
                resolution_note=None,
            )
        )
    with database.engine.connect() as connection:
        before = connection.exec_driver_sql(
            "SELECT * FROM send_attempt WHERE id='attempt-send-guard'"
        ).one()

    queue = DurableJobQueue(database, RecordingExecutor(), inter_call_delay_seconds=0)
    await queue.start()
    await queue.resume()
    await queue.stop()
    with database.engine.connect() as connection:
        after = connection.exec_driver_sql(
            "SELECT * FROM send_attempt WHERE id='attempt-send-guard'"
        ).one()
    database.dispose()
    assert after == before


def test_finished_attempt_raw_and_terminal_job_cannot_be_rewritten(database) -> None:
    session_id = add_session(database)
    with database.sessions.begin() as session:
        session.add(
            Job(
                id="immutable-job",
                session_id=session_id,
                kind=JobKind.SEARCH_PEOPLE.value,
                payload={"keywords": "immutable"},
                state="done",
                attempts=1,
                max_attempts=2,
                queued_at=NOW,
                started_at=NOW,
                finished_at=NOW,
                error=None,
                correlation_id="immutable",
                claim_token=None,
            )
        )
        session.add(
            JobAttempt(
                id="immutable-attempt",
                job_id="captured-job",
                attempt_number=1,
                worker_token="active-owner",
                started_at=NOW,
                response_received_at=NOW,
                finished_at=None,
                outcome="running",
                raw_response={"content": [], "structuredContent": {"url": "ok"}},
                raw_error=None,
                error_class=None,
                safe_error_message=None,
                retry_at=None,
            )
        )
        session.add(
            Job(
                id="captured-job",
                session_id=session_id,
                kind=JobKind.SEARCH_PEOPLE.value,
                payload={"keywords": "captured"},
                state="running",
                attempts=1,
                max_attempts=2,
                queued_at=NOW,
                started_at=NOW,
                finished_at=None,
                error=None,
                correlation_id="captured",
                claim_token="active-owner",
            )
        )

    with pytest.raises(IntegrityError, match="captured job attempt data is immutable"):
        with database.engine.begin() as connection:
            connection.exec_driver_sql(
                "UPDATE job_attempt SET raw_response=NULL WHERE id='immutable-attempt'"
            )
    with pytest.raises(IntegrityError, match="terminal job is immutable"):
        with database.sessions.begin() as session:
            session.execute(
                update(Job)
                .where(Job.id == "immutable-job")
                .values(state="queued", finished_at=None)
            )


@pytest.mark.asyncio
async def test_forged_continuation_cannot_bypass_budget(tmp_path) -> None:
    database = new_database(tmp_path / "continuation-budget.db")
    first = {
        "url": "https://www.linkedin.com/in/person/",
        "sections": {"main_profile": "profile"},
        "section_errors": {"experience": {"error_type": "rate_limit"}},
    }
    executor = RecordingExecutor([first])
    queue = DurableJobQueue(
        database,
        executor,
        inter_call_delay_seconds=0,
        rate_limit_cooldowns_seconds=(1,),
    )
    await queue.start()
    session_id = add_session(database)
    try:
        with pytest.raises(ValueError, match="generated by the worker"):
            await queue.enqueue(
                session_id,
                JobKind.GET_PERSON_PROFILE,
                {
                    "linkedin_username": "person",
                    "sections": ["experience"],
                    "parent_job_id": "forged",
                },
            )
        parent_id = await queue.enqueue(
            session_id,
            JobKind.GET_PERSON_PROFILE,
            {
                "linkedin_username": "person",
                "sections": ["main_profile", "experience", "skills"],
            },
        )
        await queue.wait_for_terminal(parent_id)
        child = next(job for job in queue.list_jobs() if job.state == "pending")
        with database.sessions.begin() as session:
            session.execute(
                update(Job)
                .where(Job.id == child.id)
                .values(
                    payload={
                        "linkedin_username": "person",
                        "sections": ["skills"],
                        "parent_job_id": parent_id,
                    }
                )
            )
        await queue.resume()
        rejected = await queue.wait_for_terminal(child.id)
        assert rejected.state == "failed"
        assert len(executor.calls) == 1
    finally:
        await queue.stop()
        database.dispose()


@pytest.mark.asyncio
async def test_sse_broker_orders_position_progress_and_terminal_events(
    tmp_path,
) -> None:
    database = new_database(tmp_path / "events.db")
    queue = DurableJobQueue(database, RecordingExecutor(), inter_call_delay_seconds=0)
    await queue.start()
    session_id = add_session(database)
    try:
        async with queue.events.subscribe() as subscriber:
            job_id = await queue.enqueue(
                session_id, JobKind.SEARCH_PEOPLE, {"keywords": "events"}
            )
            observed = []
            while True:
                event = await asyncio.wait_for(subscriber.get(), timeout=1)
                if event.data.get("id") != job_id:
                    continue
                observed.append(event)
                if event.data.get("state") == "done":
                    break
        assert [event.event for event in observed] == [
            "job",
            "job",
            "progress",
            "job",
        ]
        assert observed[0].data["position"] == 1
        assert observed[0].data["depth"] == 1
        assert observed[2].data["percent"] == 50
    finally:
        await queue.stop()
        database.dispose()


@pytest.mark.asyncio
async def test_transient_sqlite_contention_does_not_kill_worker(tmp_path) -> None:
    database = new_database(tmp_path / "contention.db")

    class OnceContendedQueue(DurableJobQueue):
        run_count = 0
        recovery_count = 0

        async def _run_claimed(self, job):
            self.run_count += 1
            if self.run_count == 1:
                raise RuntimeError("injected post-claim failure")
            await super()._run_claimed(job)

        async def _fail_if_running(self, job, error_class):
            self.recovery_count += 1
            if self.recovery_count == 1:
                raise OperationalError("UPDATE job", {}, Exception("database locked"))
            await super()._fail_if_running(job, error_class)

    executor = RecordingExecutor()
    queue = OnceContendedQueue(database, executor, inter_call_delay_seconds=0)
    await queue.start()
    session_id = add_session(database)
    try:
        first = await queue.enqueue(
            session_id, JobKind.SEARCH_PEOPLE, {"keywords": "contended"}
        )
        second = await queue.enqueue(
            session_id, JobKind.SEARCH_PEOPLE, {"keywords": "continues"}
        )
        assert (await queue.wait_for_terminal(first)).state == "failed"
        assert (await queue.wait_for_terminal(second)).state == "done"
        assert queue.recovery_count == 2
        assert len(executor.calls) == 1
    finally:
        await queue.stop()
        database.dispose()


@pytest.mark.asyncio
async def test_shutdown_is_bounded_when_executor_suppresses_cancellation(
    tmp_path,
) -> None:
    database = new_database(tmp_path / "bounded-stop.db")
    started = asyncio.Event()
    suppressed = asyncio.Event()
    finish = asyncio.Event()

    class CancellationSuppressor:
        async def execute(self, payload, capture_raw, report_progress):
            del payload, capture_raw, report_progress
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                suppressed.set()
                await finish.wait()
            return {"url": "too-late"}

    queue = DurableJobQueue(
        database,
        CancellationSuppressor(),
        inter_call_delay_seconds=0,
        shutdown_grace_seconds=0.01,
    )
    await queue.start()
    session_id = add_session(database)
    job_id = await queue.enqueue(
        session_id, JobKind.SEARCH_PEOPLE, {"keywords": "stop"}
    )
    await started.wait()
    detached = queue._worker
    assert detached is not None
    started_at = asyncio.get_running_loop().time()
    await queue.stop()
    assert asyncio.get_running_loop().time() - started_at < 0.2
    await suppressed.wait()
    with database.sessions() as session:
        job = session.get(Job, job_id)
        assert job is not None and job.state == "interrupted"
    standby = DurableJobQueue(database, RecordingExecutor(), inter_call_delay_seconds=0)
    with pytest.raises(BlockingIOError):
        await standby.start()
    finish.set()
    await detached
    await standby.start()
    await standby.stop()
    database.dispose()
