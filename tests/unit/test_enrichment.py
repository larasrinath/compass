from __future__ import annotations

import json
import random
import string
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from linkedin_dashboard.api._filters import redact_provenance_text
from linkedin_dashboard.db.models import (
    Candidate,
    DashboardSession,
    Job,
    JobAttempt,
    NavigationReservation,
    ParsedField,
    ProfileFetch,
    ProfileIdentityObservation,
    ProfileSection,
    QueueControl,
    SectionError,
    SectionReference,
)
from linkedin_dashboard.llm import NullProvider
from linkedin_dashboard.main import create_app
from linkedin_dashboard.parsing import PARSERS
from linkedin_dashboard.parsing.spans import VerifiedSpan
from linkedin_dashboard.parsing.verify import SpanProposal, verify_proposal
from linkedin_dashboard.queue.jobs import JobPayload, PersonProfilePayload
from linkedin_dashboard.queue.worker import ProgressReporter, RawCapture
from linkedin_dashboard.services.enrichment import profile_urn_routing_allowed
from linkedin_dashboard.settings import Settings
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError


class ProfileExecutor:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.calls: list[JobPayload] = []

    async def execute(
        self,
        payload: JobPayload,
        capture_raw: RawCapture,
        report_progress: ProgressReporter,
    ) -> dict[str, Any]:
        self.calls.append(payload)
        await report_progress(1, 1)
        result = self.responses.pop(0)
        raw = {
            "content": [{"type": "text", "text": "fixture"}],
            "structuredContent": result,
            "isError": False,
            "future": {"retained": True},
        }
        await capture_raw(raw, None)
        return result


class FailingProfileExecutor:
    async def execute(
        self,
        payload: JobPayload,
        capture_raw: RawCapture,
        report_progress: ProgressReporter,
    ) -> dict[str, Any]:
        del payload, capture_raw, report_progress
        raise RuntimeError("fixture read failure")


class CapturingThenFailingProfileExecutor:
    async def execute(
        self,
        payload: JobPayload,
        capture_raw: RawCapture,
        report_progress: ProgressReporter,
    ) -> dict[str, Any]:
        del payload, report_progress
        await capture_raw(
            {
                "content": [{"type": "text", "text": "captured"}],
                "structuredContent": {"unexpected": True},
                "isError": False,
            },
            None,
        )
        raise RuntimeError("fixture post-response failure")


class BlockingProfileExecutor(ProfileExecutor):
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        super().__init__(responses)
        from threading import Event

        self.entered = Event()
        self.release = Event()

    async def execute(
        self,
        payload: JobPayload,
        capture_raw: RawCapture,
        report_progress: ProgressReporter,
    ) -> dict[str, Any]:
        import asyncio

        self.entered.set()
        await asyncio.to_thread(self.release.wait, 3)
        return await super().execute(payload, capture_raw, report_progress)


def _settings(path: Path) -> Settings:
    return Settings(
        db_path=path,
        llm_provider="null",
        send_enabled=False,
        inter_call_delay_seconds=0,
    )


def _seed_candidate(app: Any, client: TestClient, username: str = "ada") -> str:
    session_id = client.post("/api/session", json={"label": "M3"}).json()["id"]
    with app.state.database.sessions.begin() as session:
        candidate = Candidate(
            id=f"candidate-{username}",
            session_id=session_id,
            username=username,
            profile_url=f"https://www.linkedin.com/in/{username}",
            display_name="Ada Lovelace",
            profile_urn=None,
            first_seen_at="2026-09-03T00:00:00+00:00",
            stage="discovered",
            retrieval_status="pending",
        )
        session.add(candidate)
    return candidate.id


def _wait(app: Any, job_id: str) -> Job:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        with app.state.database.sessions() as session:
            job = session.get(Job, job_id)
            if job is not None and job.state in {
                "done",
                "failed",
                "interrupted",
                "cancelled",
            }:
                session.expunge(job)
                return job
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def test_stage_one_stores_verbatim_history_and_code_point_spans(tmp_path) -> None:
    main = "Ada 🚀 Lovelace\nPrincipal Engineer\nChicago\nBuilds reliable systems"
    experience = "Experience\nStaff Engineer\nAnalytical Engines\n2021 - Present"
    response = {
        "url": "https://www.linkedin.com/in/ada/",
        "profile_urn": "urn:li:fsd_profile:123",
        "sections": {"main_profile": main, "experience": experience},
        "references": {
            "experience": [{"kind": "company", "url": "/company/analytical-engines/"}]
        },
    }
    executor = ProfileExecutor([response])
    app = create_app(_settings(tmp_path / "stage1.db"), queue_executor=executor)
    assert isinstance(app.state.llm_provider, NullProvider)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        candidate_id = _seed_candidate(app, client)
        queued = client.post(f"/api/candidates/{candidate_id}/enrich", json={})
        assert queued.status_code == 202
        assert queued.json()["estimated_navigations"] == 2
        job_id = queued.json()["job_id"]
        assert _wait(app, job_id).state == "done"
        detail = client.get(f"/api/candidates/{candidate_id}")
        assert detail.status_code == 200
        payload = detail.json()
        assert payload["stage"] == "stage1"
        assert payload["profile_urn"] == "urn:li:fsd_profile:123"
        assert set(payload["available_sections"]) == {
            "main_profile",
            "experience",
        }

        with app.state.database.sessions() as session:
            fetch = session.scalar(
                select(ProfileFetch).where(ProfileFetch.job_id == job_id)
            )
            assert fetch is not None
            assert fetch.raw_response == {
                "content": [{"type": "text", "text": "fixture"}],
                "structuredContent": response,
                "isError": False,
                "future": {"retained": True},
            }
            sections = list(
                session.scalars(
                    select(ProfileSection).where(
                        ProfileSection.candidate_id == candidate_id
                    )
                )
            )
            assert {row.section_name: row.raw_text for row in sections} == {
                "main_profile": main,
                "experience": experience,
            }
            fields = list(
                session.scalars(
                    select(ParsedField).where(ParsedField.candidate_id == candidate_id)
                )
            )
            raw_by_id = {row.id: row.raw_text for row in sections}
            assert fields
            assert any(field.snippet == "Principal Engineer" for field in fields)
            for field in fields:
                assert field.profile_section_id is not None
                raw = raw_by_id[field.profile_section_id]
                assert raw[field.span_start : field.span_end] == field.snippet
        assert isinstance(executor.calls[0], PersonProfilePayload)
        assert executor.calls[0].sections == ["experience"]


def test_profile_urn_is_write_once_and_conflict_quarantines_routing(tmp_path) -> None:
    def response(urn: str | None, section: str) -> dict[str, Any]:
        result: dict[str, Any] = {
            "url": "https://www.linkedin.com/in/ada/",
            "sections": {
                "main_profile": "Ada\nEngineer\nChicago",
                section: f"{section.title()}\nValue",
            },
        }
        if urn is not None:
            result["profile_urn"] = urn
        return result

    executor = ProfileExecutor(
        [
            response("urn:li:fsd_profile:one", "experience"),
            response(None, "skills"),
            response("urn:li:fsd_profile:one", "education"),
            response("urn:li:fsd_profile:two", "projects"),
        ]
    )
    app = create_app(_settings(tmp_path / "urn.db"), queue_executor=executor)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        candidate_id = _seed_candidate(app, client)
        with pytest.raises(IntegrityError, match="needs attested observation"):
            with app.state.database.engine.begin() as connection:
                connection.exec_driver_sql(
                    "UPDATE candidate SET profile_urn=? WHERE id=?",
                    ("urn:li:fsd_profile:forged", candidate_id),
                )
        with pytest.raises(
            IntegrityError, match="requires immutable profile observation"
        ):
            with app.state.database.engine.begin() as connection:
                connection.exec_driver_sql(
                    "UPDATE candidate SET profile_urn_quarantined=1 WHERE id=?",
                    (candidate_id,),
                )
        with pytest.raises(IntegrityError, match="identity already exists"):
            with app.state.database.engine.begin() as connection:
                connection.exec_driver_sql(
                    "INSERT OR REPLACE INTO candidate "
                    "SELECT * FROM candidate WHERE id=?",
                    (candidate_id,),
                )

        first = client.post(
            f"/api/candidates/{candidate_id}/enrich",
            json={"sections": ["experience"]},
        )
        assert first.status_code == 202
        assert _wait(app, first.json()["job_id"]).state == "done"
        first_detail = client.get(f"/api/candidates/{candidate_id}").json()
        assert first_detail["profile_urn"] == "urn:li:fsd_profile:one"
        assert first_detail["profile_urn_routing_allowed"] is True

        for sections in (["skills"], ["education"], ["projects"]):
            response_value = client.post(
                f"/api/candidates/{candidate_id}/enrich",
                json={"sections": sections},
            )
            assert response_value.status_code == 202
            assert _wait(app, response_value.json()["job_id"]).state == "done"

        detail = client.get(f"/api/candidates/{candidate_id}").json()
        assert detail["profile_urn"] == "urn:li:fsd_profile:one"
        assert detail["profile_urn_quarantined"] is True
        assert detail["profile_urn_routing_allowed"] is False
        with app.state.database.sessions() as session:
            observations = list(
                session.scalars(
                    select(ProfileIdentityObservation).order_by(
                        ProfileIdentityObservation.observed_at,
                        ProfileIdentityObservation.id,
                    )
                )
            )
            assert {row.verdict for row in observations} == {
                "accepted",
                "missing",
                "same",
                "conflict",
            }
            candidate = session.get(Candidate, candidate_id)
            assert candidate is not None
            assert candidate.profile_urn == "urn:li:fsd_profile:one"
            assert candidate.profile_contract_error == "profile_urn_conflict"
        with pytest.raises(IntegrityError, match="identity is immutable"):
            with app.state.database.engine.begin() as connection:
                connection.exec_driver_sql(
                    "UPDATE candidate SET profile_urn=? WHERE id=?",
                    ("urn:li:fsd_profile:forged", candidate_id),
                )


def test_returned_url_mismatch_quarantines_without_trusted_projection(tmp_path) -> None:
    executor = ProfileExecutor(
        [
            {
                "url": "https://www.linkedin.com/in/bob/",
                "profile_urn": "urn:li:fsd_profile:bob",
                "sections": {
                    "main_profile": "Bob\nEngineer\nChicago",
                    "experience": "Experience\nEngineer\nAcme",
                },
            }
        ]
    )
    app = create_app(_settings(tmp_path / "url-mismatch.db"), queue_executor=executor)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        candidate_id = _seed_candidate(app, client)
        job_id = client.post(f"/api/candidates/{candidate_id}/enrich", json={}).json()[
            "job_id"
        ]
        assert _wait(app, job_id).state == "done"
        with app.state.database.sessions() as session:
            candidate = session.get(Candidate, candidate_id)
            assert candidate is not None
            assert candidate.profile_urn is None
            assert candidate.profile_urn_quarantined is True
            fetch = session.scalar(
                select(ProfileFetch).where(ProfileFetch.job_id == job_id)
            )
            assert fetch is not None
            assert fetch.returned_url is None
            assert fetch.outcome == "error"
            assert session.scalar(select(func.count(ProfileSection.id))) == 0
            observation = session.scalar(select(ProfileIdentityObservation))
            assert observation is not None
            assert observation.verdict == "url_mismatch"


def test_concurrent_observation_and_cas_accept_exactly_one_profile_urn(
    tmp_path,
) -> None:
    app = create_app(
        _settings(tmp_path / "urn-race.db"), queue_executor=ProfileExecutor([])
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        candidate_id = _seed_candidate(app, client)
        with app.state.database.sessions() as session:
            candidate = session.get(Candidate, candidate_id)
            assert candidate is not None
            session_id = candidate.session_id

        with app.state.database.engine.begin() as connection:
            for suffix in ("one", "two"):
                job_id = f"urn-race-job-{suffix}"
                fetch_id = f"urn-race-fetch-{suffix}"
                urn = f"urn:li:fsd_profile:{suffix}"
                payload = {"linkedin_username": "ada", "sections": ["experience"]}
                projection = {
                    "url": "https://www.linkedin.com/in/ada/",
                    "profile_urn": urn,
                    "sections": {
                        "main_profile": "Ada\nEngineer\nChicago",
                        "experience": "Experience\nEngineer\nAcme",
                    },
                }
                raw = {"structuredContent": projection}
                connection.exec_driver_sql(
                    "INSERT INTO job "
                    "(id,session_id,kind,payload,state,attempts,max_attempts,queued_at,"
                    "started_at,finished_at,error,correlation_id,claim_token) VALUES "
                    "(?,?,'get_person_profile',?,'done',1,2,'now','now','now',NULL,?,NULL)",
                    (job_id, session_id, json.dumps(payload), f"correlation-{suffix}"),
                )
                connection.exec_driver_sql(
                    "INSERT INTO job_attempt "
                    "(id,job_id,attempt_number,worker_token,started_at,"
                    "response_received_at,external_call_started_at,finished_at,outcome,"
                    "raw_response,raw_error,error_class,safe_error_message,retry_at) "
                    "VALUES (?,?,1,?,'now','now','now','now','ok',?,NULL,NULL,NULL,"
                    "NULL)",
                    (
                        f"urn-race-attempt-{suffix}",
                        job_id,
                        f"worker-{suffix}",
                        json.dumps(raw),
                    ),
                )
                connection.exec_driver_sql(
                    "INSERT INTO profile_fetch "
                    "(id,candidate_id,job_id,tool,requested_sections,args,started_at,"
                    "finished_at,duration_ms,outcome,raw_response,projection_payload,"
                    "projection_source,contract_error,returned_url,processed_at,"
                    "request_stage,parent_fetch_id,root_fetch_id) VALUES "
                    "(?,?,?,'get_person_profile',?,?,'now',NULL,NULL,NULL,NULL,NULL,"
                    "NULL,NULL,NULL,NULL,'stage1',NULL,?)",
                    (
                        fetch_id,
                        candidate_id,
                        job_id,
                        json.dumps(["main_profile", "experience"]),
                        json.dumps(payload),
                        fetch_id,
                    ),
                )
                connection.exec_driver_sql(
                    "UPDATE profile_fetch SET raw_response=?,projection_payload=?,"
                    "projection_source='structured_content',returned_url=?,"
                    "finished_at='now',duration_ms=1,outcome='ok',processed_at='now' "
                    "WHERE id=?",
                    (
                        json.dumps(raw),
                        json.dumps(projection),
                        projection["url"],
                        fetch_id,
                    ),
                )

        def observe_and_assign(suffix: str) -> str:
            urn = f"urn:li:fsd_profile:{suffix}"
            with app.state.database.engine.begin() as connection:
                connection.exec_driver_sql(
                    "INSERT INTO profile_identity_observation "
                    "(id,fetch_id,candidate_id,returned_url,observed_urn,verdict,"
                    "observed_at) VALUES (?,?,?,?,?,'accepted','now')",
                    (
                        f"urn-race-observation-{suffix}",
                        f"urn-race-fetch-{suffix}",
                        candidate_id,
                        "https://www.linkedin.com/in/ada/",
                        urn,
                    ),
                )
                result = connection.exec_driver_sql(
                    "UPDATE candidate SET profile_urn=? "
                    "WHERE id=? AND profile_urn IS NULL",
                    (urn, candidate_id),
                )
                assert result.rowcount == 1
            return urn

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(observe_and_assign, suffix) for suffix in ("one", "two")
            ]
            outcomes: list[str] = []
            failures = 0
            for future in futures:
                try:
                    outcomes.append(future.result(timeout=3))
                except IntegrityError:
                    failures += 1
        assert len(outcomes) == 1
        assert failures == 1
        with app.state.database.sessions() as session:
            candidate = session.get(Candidate, candidate_id)
            assert candidate is not None and candidate.profile_urn == outcomes[0]
            assert profile_urn_routing_allowed(session, candidate) is True
            assert (
                session.scalar(select(func.count(ProfileIdentityObservation.id))) == 1
            )


@pytest.mark.parametrize(
    "malformed",
    [
        {"sections": []},
        {"section_errors": []},
        {"references": {"experience": {"kind": "company"}}},
        {"unknown_sections": ["future_section"]},
    ],
)
def test_malformed_profile_shapes_preserve_raw_but_project_nothing(
    tmp_path, malformed
) -> None:
    result: dict[str, Any] = {
        "url": "https://www.linkedin.com/in/ada/",
        "sections": {"main_profile": "Ada\nEngineer\nChicago"},
    }
    result.update(malformed)
    executor = ProfileExecutor([result])
    app = create_app(_settings(tmp_path / "malformed.db"), queue_executor=executor)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        candidate_id = _seed_candidate(app, client)
        job_id = client.post(f"/api/candidates/{candidate_id}/enrich", json={}).json()[
            "job_id"
        ]
        assert _wait(app, job_id).state == "done"
        with app.state.database.sessions() as session:
            fetch = session.scalar(
                select(ProfileFetch).where(ProfileFetch.job_id == job_id)
            )
            assert fetch is not None and fetch.raw_response is not None
            assert fetch.outcome == "error" and fetch.processed_at is not None
            assert session.scalar(select(func.count(ProfileSection.id))) == 0
            assert session.scalar(select(func.count(SectionError.id))) == 0
            assert session.scalar(select(func.count(SectionReference.id))) == 0
            candidate = session.get(Candidate, candidate_id)
            assert candidate is not None
            assert candidate.profile_urn_quarantined is False


def test_rate_limit_creates_only_missing_canonical_suffix_and_fetch_row(
    tmp_path,
) -> None:
    response = {
        "url": "https://www.linkedin.com/in/ada/",
        "sections": {
            "main_profile": "Ada\nEngineer\nChicago",
            "certifications": "Certifications\nCKA",
        },
        "section_errors": {
            "skills": {
                "error_type": "rate_limit",
                "error_message": "slow down",
                "runtime": {"cookie_path": "/private/cookies.json"},
            }
        },
    }
    executor = ProfileExecutor(
        [
            {
                "url": "https://www.linkedin.com/in/ada/",
                "sections": {
                    "main_profile": "Ada\nEngineer\nChicago",
                    "experience": "Experience\nEngineer\nAcme",
                },
            },
            response,
            {
                "url": "https://www.linkedin.com/in/ada/",
                "sections": {
                    "main_profile": "Ada\nEngineer\nChicago",
                    "skills": "Skills\nPython",
                    "projects": "Projects\nCompiler",
                },
            },
        ]
    )
    app = create_app(_settings(tmp_path / "partial.db"), queue_executor=executor)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        candidate_id = _seed_candidate(app, client)
        stage_one_id = client.post(
            f"/api/candidates/{candidate_id}/enrich", json={}
        ).json()["job_id"]
        assert _wait(app, stage_one_id).state == "done"
        queued = client.post(
            f"/api/candidates/{candidate_id}/enrich",
            json={"sections": ["projects", "skills", "certifications"]},
        )
        assert queued.status_code == 202
        parent_id = queued.json()["job_id"]
        assert _wait(app, parent_id).state == "done"
        with app.state.database.sessions() as session:
            child = session.scalar(
                select(Job).where(Job.state == "pending", Job.id != parent_id)
            )
            assert child is not None
            assert child.payload == {
                "linkedin_username": "ada",
                "sections": ["skills", "projects"],
                "parent_job_id": parent_id,
            }
            child_fetch = session.scalar(
                select(ProfileFetch).where(ProfileFetch.job_id == child.id)
            )
            assert child_fetch is not None
            assert child_fetch.requested_sections == [
                "main_profile",
                "skills",
                "projects",
            ]
            assert child_fetch.request_stage == "resume"
            assert (
                session.scalar(
                    select(func.count(ProfileSection.id)).where(
                        ProfileSection.fetch_id == child_fetch.parent_fetch_id
                    )
                )
                == 2
            )
            error = session.scalar(
                select(SectionError).where(SectionError.fetch_id.is_not(None))
            )
            assert error is not None
            assert "runtime" not in (error.extra or {})
            dashboard_session = session.get(DashboardSession, child.session_id)
            assert dashboard_session is not None and dashboard_session.nav_used == 5
        assert client.post("/api/queue/resume").status_code == 200
        assert _wait(app, child.id).state == "done"
        with app.state.database.sessions() as session:
            dashboard_session = session.get(DashboardSession, child.session_id)
            assert dashboard_session is not None and dashboard_session.nav_used == 8


def test_rate_limit_on_implicit_main_refunds_all_skipped_explicit_sections(
    tmp_path,
) -> None:
    executor = ProfileExecutor(
        [
            {
                "url": "https://www.linkedin.com/in/ada/",
                "sections": {},
                "section_errors": {
                    "main_profile": {
                        "error_type": "rate_limit",
                        "error_message": "slow down",
                    }
                },
            },
            {
                "url": "https://www.linkedin.com/in/ada/",
                "sections": {
                    "main_profile": "Ada\nEngineer\nChicago",
                    "experience": "Experience\nEngineer\nAcme",
                },
            },
        ]
    )
    app = create_app(_settings(tmp_path / "main-rate.db"), queue_executor=executor)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        candidate_id = _seed_candidate(app, client)
        parent_id = client.post(
            f"/api/candidates/{candidate_id}/enrich", json={}
        ).json()["job_id"]
        assert _wait(app, parent_id).state == "done"
        with app.state.database.sessions() as session:
            child = session.scalar(select(Job).where(Job.state == "pending"))
            assert child is not None
            dashboard_session = session.get(DashboardSession, child.session_id)
            assert dashboard_session is not None and dashboard_session.nav_used == 1
        assert client.post("/api/queue/resume").status_code == 200
        assert _wait(app, child.id).state == "done"
        with app.state.database.sessions() as session:
            dashboard_session = session.get(DashboardSession, child.session_id)
            assert dashboard_session is not None and dashboard_session.nav_used == 3
            candidate = session.get(Candidate, candidate_id)
            assert candidate is not None and candidate.stage == "stage1"


def test_failed_stage_one_retains_candidate_and_terminal_fetch(tmp_path) -> None:
    app = create_app(
        _settings(tmp_path / "failed-stage1.db"),
        queue_executor=FailingProfileExecutor(),
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        candidate_id = _seed_candidate(app, client)
        job_id = client.post(f"/api/candidates/{candidate_id}/enrich", json={}).json()[
            "job_id"
        ]
        assert _wait(app, job_id).state == "failed"
        with app.state.database.sessions() as session:
            candidate = session.get(Candidate, candidate_id)
            assert candidate is not None
            assert candidate.stage == "discovered"
            assert candidate.retrieval_status == "failed"
            fetch = session.scalar(
                select(ProfileFetch).where(ProfileFetch.job_id == job_id)
            )
            assert fetch is not None
            assert fetch.outcome == "error"
            assert fetch.finished_at is not None
            assert fetch.raw_response is None
            dashboard_session = session.get(DashboardSession, candidate.session_id)
            assert dashboard_session is not None and dashboard_session.nav_used == 2


def test_profile_failure_after_response_does_not_refund_navigation(tmp_path) -> None:
    app = create_app(
        _settings(tmp_path / "captured-failure.db"),
        queue_executor=CapturingThenFailingProfileExecutor(),
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        candidate_id = _seed_candidate(app, client)
        job_id = client.post(f"/api/candidates/{candidate_id}/enrich", json={}).json()[
            "job_id"
        ]
        assert _wait(app, job_id).state == "failed"
        with app.state.database.sessions() as session:
            candidate = session.get(Candidate, candidate_id)
            assert candidate is not None
            dashboard_session = session.get(DashboardSession, candidate.session_id)
            assert dashboard_session is not None and dashboard_session.nav_used == 2


def test_unknown_sections_are_loud_and_never_retried(tmp_path, caplog) -> None:
    executor = ProfileExecutor(
        [
            {
                "url": "https://www.linkedin.com/in/ada/",
                "sections": {"main_profile": "Ada\nEngineer\nChicago"},
                "unknown_sections": ["future_section"],
            }
        ]
    )
    app = create_app(_settings(tmp_path / "unknown.db"), queue_executor=executor)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        candidate_id = _seed_candidate(app, client)
        job_id = client.post(f"/api/candidates/{candidate_id}/enrich", json={}).json()[
            "job_id"
        ]
        assert _wait(app, job_id).state == "done"
        assert len(executor.calls) == 1
        assert "Profile contract failure" in caplog.text
        with app.state.database.sessions() as session:
            fetch = session.scalar(
                select(ProfileFetch).where(ProfileFetch.job_id == job_id)
            )
            assert fetch is not None
            assert fetch.contract_error == "profile_contract_error"
            assert fetch.outcome == "error"
            assert session.scalar(select(func.count(ProfileSection.id))) == 0


def test_stage_two_cap_validation_and_upstream_order(tmp_path) -> None:
    app = create_app(
        _settings(tmp_path / "validation.db"),
        queue_executor=ProfileExecutor(
            [
                {
                    "url": "https://www.linkedin.com/in/ada/",
                    "sections": {
                        "main_profile": "Ada\nEngineer\nChicago",
                        "experience": "Experience\nEngineer\nAcme",
                    },
                }
            ]
        ),
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        candidate_id = _seed_candidate(app, client)
        too_many = client.post(
            f"/api/candidates/{candidate_id}/enrich",
            json={"sections": ["skills", "projects", "education", "certifications"]},
        )
        assert too_many.status_code == 422
        assert "at most 3" in too_many.json()["detail"]
        unknown = client.post(
            f"/api/candidates/{candidate_id}/enrich",
            json={"sections": ["made_up"]},
        )
        assert unknown.status_code == 422
        premature = client.post(
            f"/api/candidates/{candidate_id}/enrich",
            json={"sections": ["skills"]},
        )
        assert premature.status_code == 422
        assert "completed Stage 1" in premature.json()["detail"]
        stage_one_id = client.post(
            f"/api/candidates/{candidate_id}/enrich", json={}
        ).json()["job_id"]
        assert _wait(app, stage_one_id).state == "done"
        queued = client.post(
            f"/api/candidates/{candidate_id}/enrich",
            json={"sections": ["projects", "education", "skills"]},
        )
        assert queued.status_code == 202
        with app.state.database.sessions() as session:
            job = session.get(Job, queued.json()["job_id"])
            assert job is not None
            assert job.payload["sections"] == ["education", "skills", "projects"]


def test_concurrent_enrichment_admits_exactly_one_active_fetch(tmp_path) -> None:
    executor = BlockingProfileExecutor(
        [
            {
                "url": "https://www.linkedin.com/in/ada/",
                "sections": {
                    "main_profile": "Ada\nEngineer\nChicago",
                    "experience": "Experience\nEngineer\nAcme",
                },
            }
        ]
    )
    app = create_app(_settings(tmp_path / "concurrent.db"), queue_executor=executor)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        candidate_id = _seed_candidate(app, client)

        def enqueue() -> int:
            return client.post(
                f"/api/candidates/{candidate_id}/enrich", json={}
            ).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(enqueue)
            second = pool.submit(enqueue)
            statuses = [first.result(timeout=3), second.result(timeout=3)]
        assert sorted(statuses) == [202, 409]
        assert executor.entered.wait(3)
        executor.release.set()


def test_budget_exhaustion_and_cancel_finish_fetch_history_immediately(
    tmp_path,
) -> None:
    executor = ProfileExecutor([])
    app = create_app(_settings(tmp_path / "terminal.db"), queue_executor=executor)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        budget_candidate = _seed_candidate(app, client, "budget")
        with app.state.database.sessions.begin() as session:
            candidate = session.get(Candidate, budget_candidate)
            assert candidate is not None
            dashboard_session = session.get(DashboardSession, candidate.session_id)
            assert dashboard_session is not None
            dashboard_session.nav_used = dashboard_session.nav_budget
        rejected = client.post(f"/api/candidates/{budget_candidate}/enrich", json={})
        assert rejected.status_code == 409
        with app.state.database.sessions() as session:
            assert session.scalar(select(func.count(ProfileFetch.id))) == 0
            assert session.scalar(select(func.count(Job.id))) == 0

        cancelled_candidate = _seed_candidate(app, client, "cancelled")
        with app.state.database.sessions.begin() as session:
            control = session.get(QueueControl, 1)
            assert control is not None
            control.state = "paused"
            control.pause_reason = "AUTH_REQUIRED"
            control.resume_at = None
            control.operator_resume_required = True
        cancelled_job = client.post(
            f"/api/candidates/{cancelled_candidate}/enrich", json={}
        ).json()["job_id"]
        cancelled = client.post(f"/api/jobs/{cancelled_job}/cancel")
        assert cancelled.status_code == 200
        with app.state.database.sessions() as session:
            fetch = session.scalar(
                select(ProfileFetch).where(ProfileFetch.job_id == cancelled_job)
            )
            assert fetch is not None and fetch.outcome == "error"
            assert fetch.finished_at is not None


def test_terminal_null_fetch_cannot_be_forged_into_identity_authority(tmp_path) -> None:
    good_projection = {
        "url": "https://www.linkedin.com/in/legit/",
        "profile_urn": "urn:li:fsd_profile:legit",
        "sections": {
            "main_profile": "Legit\nEngineer\nChicago",
            "experience": "Experience\nEngineer\nAcme",
        },
    }
    malformed_projection = {
        "url": "https://www.linkedin.com/in/malformed/",
        "profile_urn": "urn:li:fsd_profile:malformed",
        "sections": [],
    }
    app = create_app(
        _settings(tmp_path / "terminal-authority.db"),
        queue_executor=ProfileExecutor([good_projection, malformed_projection]),
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        legit_id = _seed_candidate(app, client, "legit")
        legit_job = client.post(f"/api/candidates/{legit_id}/enrich", json={}).json()[
            "job_id"
        ]
        assert _wait(app, legit_job).state == "done"
        legit_detail = client.get(f"/api/candidates/{legit_id}").json()
        assert legit_detail["profile_urn_routing_allowed"] is True
        with app.state.database.sessions() as session:
            legit_fetch = session.scalar(
                select(ProfileFetch).where(ProfileFetch.job_id == legit_job)
            )
            assert legit_fetch is not None
            assert legit_fetch.raw_response is not None
            assert legit_fetch.projection_payload == good_projection
            assert legit_fetch.projection_source == "structured_content"

        malformed_id = _seed_candidate(app, client, "malformed")
        malformed_job = client.post(
            f"/api/candidates/{malformed_id}/enrich", json={}
        ).json()["job_id"]
        assert _wait(app, malformed_job).state == "done"
        with app.state.database.sessions() as session:
            malformed_fetch = session.scalar(
                select(ProfileFetch).where(ProfileFetch.job_id == malformed_job)
            )
            assert malformed_fetch is not None
            assert malformed_fetch.raw_response is not None
            assert malformed_fetch.contract_error == "profile_contract_error"
            malformed_fetch_id = malformed_fetch.id
        with pytest.raises(IntegrityError, match="requires an attested fetch"):
            with app.state.database.engine.begin() as connection:
                connection.exec_driver_sql(
                    "INSERT INTO profile_identity_observation "
                    "(id,fetch_id,candidate_id,returned_url,observed_urn,verdict,"
                    "observed_at) VALUES "
                    "('forged-malformed-observation',?,?,?,?,'accepted','now')",
                    (
                        malformed_fetch_id,
                        malformed_id,
                        malformed_projection["url"],
                        malformed_projection["profile_urn"],
                    ),
                )
        assert (
            client.get(f"/api/candidates/{malformed_id}").json()[
                "profile_urn_routing_allowed"
            ]
            is False
        )

        with app.state.database.sessions.begin() as session:
            control = session.get(QueueControl, 1)
            assert control is not None
            control.state = "paused"
            control.pause_reason = "AUTH_REQUIRED"
            control.resume_at = None
            control.operator_resume_required = True
        pending_id = _seed_candidate(app, client, "pending-forge")
        pending_job = client.post(
            f"/api/candidates/{pending_id}/enrich", json={}
        ).json()["job_id"]
        with app.state.database.sessions() as session:
            pending_fetch = session.scalar(
                select(ProfileFetch).where(ProfileFetch.job_id == pending_job)
            )
            assert pending_fetch is not None and pending_fetch.raw_response is None
            pending_fetch_id = pending_fetch.id
        forged_projection = {
            "url": "https://www.linkedin.com/in/pending-forge/",
            "profile_urn": "urn:li:fsd_profile:forged",
            "sections": {},
        }
        with pytest.raises(IntegrityError, match="projection_requires_raw"):
            with app.state.database.engine.begin() as connection:
                connection.exec_driver_sql(
                    "UPDATE profile_fetch SET projection_payload=?,"
                    "projection_source='structured_content' WHERE id=?",
                    (json.dumps(forged_projection), pending_fetch_id),
                )

        assert client.post(f"/api/jobs/{pending_job}/cancel").status_code == 200
        with pytest.raises(IntegrityError, match="terminal profile projection"):
            with app.state.database.engine.begin() as connection:
                connection.exec_driver_sql(
                    "UPDATE profile_fetch SET raw_response=?,projection_payload=?,"
                    "projection_source='structured_content' WHERE id=?",
                    (
                        json.dumps({"structuredContent": forged_projection}),
                        json.dumps(forged_projection),
                        pending_fetch_id,
                    ),
                )
        with pytest.raises(
            IntegrityError,
            match=r"history already exists|requires exact job, candidate, and request",
        ):
            with app.state.database.engine.begin() as connection:
                connection.exec_driver_sql(
                    "INSERT OR REPLACE INTO profile_fetch "
                    "SELECT * FROM profile_fetch WHERE id=?",
                    (pending_fetch_id,),
                )
        with pytest.raises(IntegrityError, match="requires an attested fetch"):
            with app.state.database.engine.begin() as connection:
                connection.exec_driver_sql(
                    "INSERT INTO profile_identity_observation "
                    "(id,fetch_id,candidate_id,returned_url,observed_urn,verdict,"
                    "observed_at) VALUES "
                    "('forged-cancelled-observation',?,?,?,?,'accepted','now')",
                    (
                        pending_fetch_id,
                        pending_id,
                        forged_projection["url"],
                        forged_projection["profile_urn"],
                    ),
                )
        pending_detail = client.get(f"/api/candidates/{pending_id}").json()
        assert pending_detail["profile_urn"] is None
        assert pending_detail["profile_urn_routing_allowed"] is False


def test_paused_admission_reserves_budget_and_batch_rolls_back_wholly(tmp_path) -> None:
    app = create_app(
        _settings(tmp_path / "admission.db"), queue_executor=ProfileExecutor([])
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        first_id = _seed_candidate(app, client, "first")
        with app.state.database.sessions.begin() as session:
            first = session.get(Candidate, first_id)
            assert first is not None
            dashboard_session = session.get(DashboardSession, first.session_id)
            control = session.get(QueueControl, 1)
            assert dashboard_session is not None and control is not None
            dashboard_session.nav_budget = 2
            control.state = "paused"
            control.pause_reason = "AUTH_REQUIRED"
            control.operator_resume_required = True
            for username in ("second", "third"):
                session.add(
                    Candidate(
                        id=f"candidate-{username}",
                        session_id=first.session_id,
                        username=username,
                        profile_url=f"https://www.linkedin.com/in/{username}",
                        display_name=username.title(),
                        profile_urn=None,
                        first_seen_at="2026-09-03T00:00:00+00:00",
                        stage="discovered",
                        retrieval_status="pending",
                    )
                )
        accepted = client.post(f"/api/candidates/{first_id}/enrich", json={})
        assert accepted.status_code == 202
        rejected = client.post("/api/candidates/candidate-second/enrich", json={})
        assert rejected.status_code == 409
        with app.state.database.sessions() as session:
            assert session.scalar(select(func.count(Job.id))) == 1
            reservation = session.scalar(select(NavigationReservation))
            assert reservation is not None and reservation.state == "reserved"

        assert (
            client.post(f"/api/jobs/{accepted.json()['job_id']}/cancel").status_code
            == 200
        )
        batch = client.post(
            "/api/candidates/enrich-batch",
            json={
                "candidate_ids": ["candidate-second", "candidate-third"],
                "sections": ["experience"],
            },
        )
        assert batch.status_code == 409
        with app.state.database.sessions() as session:
            assert (
                session.scalar(
                    select(func.count(ProfileFetch.id)).where(
                        ProfileFetch.candidate_id.in_(
                            ("candidate-second", "candidate-third")
                        )
                    )
                )
                == 0
            )


def test_all_parsers_are_total_and_verified_span_cannot_be_forged() -> None:
    randomizer = random.Random(431)
    for parser in PARSERS.values():
        for _ in range(100):
            raw = "".join(
                randomizer.choice(string.printable + "🚀é")
                for _ in range(randomizer.randrange(0, 300))
            )
            fields = parser(raw)
            for field in fields:
                assert raw[field.span.start : field.span.end] == field.span.snippet
    with pytest.raises(TypeError, match="exact verification"):
        VerifiedSpan(0, 4, "fake")


def test_representative_experience_fixture_recall_has_explicit_denominator() -> None:
    fixture_root = Path(__file__).parents[1] / "fixtures" / "profile_parsing"
    corpus = json.loads((fixture_root / "corpus.json").read_text())
    gold = json.loads((fixture_root / "gold.json").read_text())
    assert len(corpus["profiles"]) == 8
    assert corpus["provenance"] == "synthetic_representative_not_recorded"
    gold_by_id = {item["id"]: item for item in gold["profiles"]}
    matched = 0
    denominator = 0
    for profile in corpus["profiles"]:
        parsed = PARSERS["experience"](profile["experience"])
        values_by_kind = {
            "titles": {
                field.value for field in parsed if field.field_key.endswith(".title")
            },
            "companies": {
                field.value for field in parsed if field.field_key.endswith(".company")
            },
        }
        expected = gold_by_id[profile["id"]]
        for kind in ("titles", "companies"):
            denominator += len(expected[kind])
            matched += sum(value in values_by_kind[kind] for value in expected[kind])
    assert denominator == 16
    assert matched / denominator >= 0.90


def test_experience_parser_anchors_roles_and_inherits_group_company() -> None:
    fixture_path = (
        Path(__file__).parents[1]
        / "fixtures"
        / "profile_parsing"
        / "layout_regressions.json"
    )
    fixture = json.loads(fixture_path.read_text())
    assert fixture["provenance"] == "synthetic_structural_not_recorded"

    for case in fixture["cases"]:
        parsed = PARSERS["experience"](case["experience"])
        entries: dict[str, dict[str, str]] = {}
        for field in parsed:
            prefix, entry, kind = field.field_key.split(".")
            assert prefix == "experience"
            entries.setdefault(entry, {})[kind] = field.value
            assert (
                case["experience"][field.span.start : field.span.end]
                == field.span.snippet
                == field.value
            )

        assert [
            [entry["title"], entry["company"]] for entry in entries.values()
        ] == case["pairs"]


def test_llm_proposal_requires_exact_substring() -> None:
    exact = verify_proposal(
        "Uses Kubernetes daily",
        SpanProposal("skill", "Kubernetes", "Kubernetes", "skills"),
    )
    assert exact.origin == "llm_verified"
    assert exact.span is not None
    almost = verify_proposal(
        "Uses Kubernetes daily",
        SpanProposal("skill", "Kubernetes", "kubernetes", "skills"),
    )
    assert almost.origin == "llm_unverified"
    assert almost.span is None


def test_database_rejects_unrooted_or_inexact_parsed_fields(database) -> None:
    with pytest.raises(IntegrityError, match="exact profile section span"):
        with database.sessions.begin() as session:
            session.add(
                ParsedField(
                    id="forged",
                    candidate_id="missing",
                    field_key="name",
                    value="Invented",
                    section_name="main_profile",
                    span_start=0,
                    span_end=8,
                    snippet="Invented",
                    origin="deterministic",
                    parser_version="forged",
                    created_at="now",
                    profile_section_id=None,
                )
            )


def test_raw_endpoint_preserves_offsets_and_withholds_overlapping_private_span(
    tmp_path,
) -> None:
    private_line = "cookie_path: /Users/operator/.linkedin-mcp/profile/cookies.json"
    main = f"{private_line}\nPrincipal 🚀 Engineer\nChicago"
    executor = ProfileExecutor(
        [
            {
                "url": "https://www.linkedin.com/in/ada/",
                "sections": {
                    "main_profile": main,
                    "experience": "Experience\nEngineer\nAcme",
                },
            }
        ]
    )
    app = create_app(_settings(tmp_path / "redaction.db"), queue_executor=executor)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        candidate_id = _seed_candidate(app, client)
        job_id = client.post(f"/api/candidates/{candidate_id}/enrich", json={}).json()[
            "job_id"
        ]
        assert _wait(app, job_id).state == "done"
        detail = client.get(f"/api/candidates/{candidate_id}").json()
        hidden_detail = next(
            item for item in detail["fields"] if item["field_key"] == "name"
        )
        assert hidden_detail["value"] is None
        assert hidden_detail["snippet"] is None
        assert hidden_detail["span_start"] is None
        assert hidden_detail["provenance_label"] == "Provenance withheld"
        visible_detail = next(
            item for item in detail["fields"] if item["field_key"] == "headline"
        )
        response = client.get(f"/api/candidates/{candidate_id}/sections/main_profile")
        assert response.status_code == 200
        payload = response.json()
        assert payload["span_unit"] == "unicode_code_point"
        assert len(payload["raw_text"]) == len(main)
        assert "/Users/operator" not in payload["raw_text"]
        assert ".linkedin-mcp" not in payload["raw_text"]
        withheld = next(
            item for item in payload["spans"] if item["field_key"] == "name"
        )
        assert withheld == {
            "id": withheld["id"],
            "field_key": "name",
            "profile_section_id": payload["profile_section_id"],
            "span_start": None,
            "span_end": None,
            "value": None,
            "snippet": None,
            "verbatim": None,
            "provenance_available": False,
            "provenance_label": "Provenance withheld",
        }
        visible = next(
            item for item in payload["spans"] if item["field_key"] == "headline"
        )
        assert visible["provenance_available"] is True
        assert visible["verbatim"] == "Principal 🚀 Engineer"
        assert (
            payload["raw_text"][visible["span_start"] : visible["span_end"]]
            == visible["verbatim"]
        )
        assert visible_detail["profile_section_id"] == payload["profile_section_id"]
        assert (
            payload["raw_text"][
                visible_detail["span_start"] : visible_detail["span_end"]
            ]
            == visible_detail["value"]
            == visible_detail["snippet"]
        )

        with app.state.database.sessions() as session:
            section_id = payload["profile_section_id"]
            stored = session.get(ProfileSection, section_id)
            assert stored is not None and stored.raw_text == main
        with pytest.raises(
            IntegrityError, match="profile section history is immutable"
        ):
            with app.state.database.sessions.begin() as session:
                stored = session.get(ProfileSection, payload["profile_section_id"])
                assert stored is not None
                stored.raw_text = "rewritten"


def test_provenance_redaction_keeps_linkedin_relative_paths_verbatim() -> None:
    raw = "See /in/ada-lovelace/ and /company/analytical-engines/"
    redacted, ranges = redact_provenance_text(raw)
    assert redacted == raw
    assert ranges == ()


def test_m3_history_rejects_update_delete_replace_and_forged_projection(
    tmp_path,
) -> None:
    result = {
        "url": "https://www.linkedin.com/in/ada/",
        "sections": {"main_profile": "Ada\nEngineer\nChicago"},
        "section_errors": {
            "experience": {
                "error_type": "timeout",
                "error_message": "section timed out",
            }
        },
        "references": {
            "main_profile": [{"kind": "person", "url": "/in/ada/", "text": "Ada"}]
        },
    }
    executor = ProfileExecutor([result])
    app = create_app(_settings(tmp_path / "history.db"), queue_executor=executor)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        candidate_id = _seed_candidate(app, client)
        job_id = client.post(f"/api/candidates/{candidate_id}/enrich", json={}).json()[
            "job_id"
        ]
        assert _wait(app, job_id).state == "done"

        with app.state.database.sessions() as session:
            attempt = session.scalar(
                select(JobAttempt).where(JobAttempt.job_id == job_id)
            )
            fetch = session.scalar(
                select(ProfileFetch).where(ProfileFetch.job_id == job_id)
            )
            section = session.scalar(select(ProfileSection))
            error = session.scalar(select(SectionError))
            reference = session.scalar(select(SectionReference))
            field = session.scalar(select(ParsedField))
            observation = session.scalar(select(ProfileIdentityObservation))
            candidate = session.get(Candidate, candidate_id)
            assert all(
                row is not None
                for row in (
                    attempt,
                    fetch,
                    section,
                    error,
                    reference,
                    field,
                    observation,
                    candidate,
                )
            )
            row_ids = {
                "job": job_id,
                "job_attempt": attempt.id,
                "profile_fetch": fetch.id,
                "profile_section": section.id,
                "section_error": error.id,
                "section_reference": reference.id,
                "parsed_field": field.id,
                "profile_identity_observation": observation.id,
            }
            session_id = candidate.session_id

        mutations = {
            "job": "UPDATE job SET payload='{}' WHERE id=?",
            "job_attempt": "UPDATE job_attempt SET worker_token='forged' WHERE id=?",
            "profile_fetch": (
                "UPDATE profile_fetch SET returned_url='/in/bob' WHERE id=?"
            ),
            "profile_section": (
                "UPDATE profile_section SET raw_text='forged' WHERE id=?"
            ),
            "section_error": (
                "UPDATE section_error SET error_message='forged' WHERE id=?"
            ),
            "section_reference": (
                "UPDATE section_reference SET text='forged' WHERE id=?"
            ),
            "parsed_field": "UPDATE parsed_field SET value='forged' WHERE id=?",
            "profile_identity_observation": (
                "UPDATE profile_identity_observation SET verdict='missing' WHERE id=?"
            ),
        }
        for table, row_id in row_ids.items():
            for statement in (
                mutations[table],
                f"DELETE FROM {table} WHERE id=?",
                f"INSERT OR REPLACE INTO {table} SELECT * FROM {table} WHERE id=?",
            ):
                try:
                    with app.state.database.engine.begin() as connection:
                        connection.exec_driver_sql(statement, (row_id,))
                except IntegrityError:
                    continue
                raise AssertionError(f"unguarded mutation: {table}: {statement}")

        with pytest.raises(IntegrityError, match="exact committed fetch content"):
            with app.state.database.engine.begin() as connection:
                connection.exec_driver_sql(
                    "INSERT INTO profile_section "
                    "(id,candidate_id,fetch_id,section_name,raw_text,retrieved_at,"
                    "char_len) "
                    "VALUES ('forged-section',?,?, 'main_profile','Invented','now',8)",
                    (candidate_id, row_ids["profile_fetch"]),
                )

        with pytest.raises(IntegrityError, match="profile projection is immutable"):
            with app.state.database.engine.begin() as connection:
                connection.exec_driver_sql(
                    "UPDATE profile_fetch SET contract_error='forged' WHERE id=?",
                    (row_ids["profile_fetch"],),
                )
        with pytest.raises(IntegrityError, match="source item already exists"):
            with app.state.database.engine.begin() as connection:
                connection.exec_driver_sql(
                    "INSERT INTO section_error "
                    "(id,candidate_id,search_run_id,fetch_id,section_name,error_type,"
                    "error_message,extra,source_item) SELECT 'forged-error',"
                    "candidate_id,search_run_id,fetch_id,section_name,error_type,"
                    "error_message,extra,source_item FROM section_error WHERE id=?",
                    (row_ids["section_error"],),
                )
        with pytest.raises(IntegrityError, match="source position already exists"):
            with app.state.database.engine.begin() as connection:
                connection.exec_driver_sql(
                    "INSERT INTO section_reference "
                    "(id,candidate_id,section_name,kind,url,text,context,value,fetch_id,"
                    "source_position) SELECT 'forged-reference',candidate_id,"
                    "section_name,kind,url,text,context,value,fetch_id,source_position "
                    "FROM section_reference WHERE id=?",
                    (row_ids["section_reference"],),
                )

        with app.state.database.engine.begin() as connection:
            connection.exec_driver_sql("DELETE FROM session WHERE id=?", (session_id,))
            assert (
                connection.exec_driver_sql(
                    "SELECT count(*) FROM profile_fetch WHERE id=?",
                    (row_ids["profile_fetch"],),
                ).scalar_one()
                == 0
            )
