from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from linkedin_dashboard.db.models import (
    Candidate,
    CandidateSource,
    Job,
    JobAttempt,
    QueueControl,
    RoleBrief,
    SearchRun,
)
from linkedin_dashboard.main import create_app
from linkedin_dashboard.parsing.identity import (
    InvalidPersonReference,
    normalize_person_reference,
)
from linkedin_dashboard.queue.jobs import JobPayload
from linkedin_dashboard.queue.worker import ProgressReporter, RawCapture
from linkedin_dashboard.settings import Settings
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError


class FixtureExecutor:
    def __init__(self, responses: list[dict[str, Any]] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[JobPayload] = []

    async def execute(
        self,
        payload: JobPayload,
        capture_raw: RawCapture,
        report_progress: ProgressReporter,
    ) -> dict[str, Any]:
        self.calls.append(payload)
        await report_progress(1, 1)
        result = self.responses.pop(0) if self.responses else {"url": "fixture"}
        raw = {
            "content": [{"type": "text", "text": "fixture"}],
            "structuredContent": result,
            "isError": False,
            "futureProtocolField": {"preserved": True},
        }
        await capture_raw(raw, None)
        return result


class TimeoutThenFixtureExecutor(FixtureExecutor):
    def __init__(self, result: dict[str, Any]) -> None:
        super().__init__([result])
        self.timed_out = False

    async def execute(
        self,
        payload: JobPayload,
        capture_raw: RawCapture,
        report_progress: ProgressReporter,
    ) -> dict[str, Any]:
        if not self.timed_out:
            self.timed_out = True
            self.calls.append(payload)
            raise TimeoutError
        return await super().execute(payload, capture_raw, report_progress)


def settings(path: Path) -> Settings:
    return Settings(
        db_path=path,
        llm_provider="null",
        send_enabled=False,
        inter_call_delay_seconds=0,
    )


def start_session(client: TestClient) -> str:
    response = client.post("/api/session", json={"label": "Platform engineering"})
    assert response.status_code == 201
    return str(response.json()["id"])


def brief_payload(session_id: str) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "job_description": "Build reliable developer platforms.",
        "required_skills": [
            {"term": "Kubernetes", "aliases": ["k8s"]},
            {"term": "Python", "aliases": []},
        ],
        "optional_skills": [{"term": "Terraform", "aliases": ["OpenTofu"]}],
        "target_titles": [
            {"term": "Platform Engineer", "aliases": ["DevOps Engineer"]}
        ],
        "location": "Chicago",
        "industries": [{"term": "Fintech", "aliases": ["Financial technology"]}],
        "positive_keywords": ["SRE", "reliability"],
        "negative_keywords": ["intern"],
        "message_tone": "Warm and direct",
    }


def wait_for_job(app: Any, job_id: str) -> Job:
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


def test_brief_round_trip_aliases_versioning_and_protected_term(tmp_path) -> None:
    app = create_app(settings(tmp_path / "brief.db"), queue_executor=FixtureExecutor())
    with TestClient(app, base_url="http://127.0.0.1") as client:
        session_id = start_session(client)
        payload = brief_payload(session_id)
        first = client.post("/api/briefs", json=payload)
        assert first.status_code == 201
        assert first.json()["version"] == 1
        assert first.json()["required_skills"] == payload["required_skills"]
        assert first.json()["optional_skills"] == payload["optional_skills"]
        assert first.json()["target_titles"] == payload["target_titles"]
        assert first.json()["industries"] == payload["industries"]
        assert first.json()["target_titles"][0]["aliases"] == ["DevOps Engineer"]
        assert first.json()["industries"][0]["aliases"] == ["Financial technology"]
        current = client.get("/api/briefs/current", params={"session_id": session_id})
        assert current.json() == first.json()

        payload["message_tone"] = "Plain-spoken"
        second = client.put("/api/briefs/current", json=payload)
        assert second.status_code == 200
        assert second.json()["version"] == 2
        with app.state.database.sessions() as db_session:
            versions = list(
                db_session.scalars(
                    select(RoleBrief)
                    .where(RoleBrief.session_id == session_id)
                    .order_by(RoleBrief.version)
                )
            )
            assert len(versions) == 2
            assert versions[0].superseded_at is not None
            first_id = versions[0].id

        with (
            pytest.raises(IntegrityError),
            app.state.database.sessions.begin() as db_session,
        ):
            historical = db_session.get(RoleBrief, first_id)
            assert historical is not None
            historical.job_description = "Mutated history"

        blocked = brief_payload(session_id)
        blocked["required_skills"][0]["aliases"] = ["gender"]
        blocked["positive_keywords"] = ["national_origin"]
        response = client.put("/api/briefs/current", json=blocked)
        assert response.status_code == 422
        assert response.json()["detail"]["offending_terms"] == [
            {"field": "required_skills.0.aliases.0", "term": "gender"},
            {"field": "positive_keywords.0", "term": "national_origin"},
        ]
        with app.state.database.sessions() as db_session:
            assert (
                db_session.scalar(
                    select(func.count(RoleBrief.id)).where(
                        RoleBrief.session_id == session_id
                    )
                )
                == 2
            )


def test_search_validation_rejects_before_queueing(tmp_path) -> None:
    executor = FixtureExecutor()
    app = create_app(settings(tmp_path / "validation.db"), queue_executor=executor)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        session_id = start_session(client)
        brief = client.post("/api/briefs", json=brief_payload(session_id)).json()
        base = {
            "session_id": session_id,
            "brief_id": brief["id"],
            "keywords": "platform engineer",
        }
        network = client.post("/api/searches", json={**base, "network": ["X"]})
        assert network.status_code == 422
        assert (
            network.json()["detail"]
            == "Invalid network token(s) ['X']; expected any of ['F', 'S', 'O']"
        )
        company = client.post("/api/searches", json={**base, "current_company": "Acme"})
        assert company.status_code == 422
        assert company.json()["detail"] == (
            "current_company must be a numeric LinkedIn company URN id "
            "(e.g. '1115' for SAP); got 'Acme'. Plain-text company names are "
            "silently ignored by LinkedIn. Look up the URN via "
            'get_company_profile -> references["about"].'
        )
        protected = client.post(
            "/api/searches", json={**base, "keywords": "platform gender"}
        )
        assert protected.status_code == 422
        assert protected.json()["detail"] == (
            "keywords contain a protected sourcing criterion"
        )
        with app.state.database.sessions() as db_session:
            assert db_session.scalar(select(func.count(SearchRun.id))) == 0
        assert executor.calls == []


def test_raw_cap_dedupe_provenance_and_diagnostic_privacy(tmp_path) -> None:
    refs: list[dict[str, Any]] = [
        {
            "kind": "person",
            "url": "/in/Alice/",
            "text": "Alice Example",
            "context": "Platform Engineer · Key: Kubernetes",
        },
        {"kind": "person", "url": "/in/bob/", "text": "Bob Example"},
        {"kind": "person", "url": "/in/cara/", "text": "Cara Example"},
        {"kind": "person", "url": "/in/dan/", "text": "Dan Example"},
        {"kind": "person", "url": "/in/erin/", "text": "Erin Example"},
        {"kind": "person", "url": "/in/frank/", "text": "Frank Example"},
    ]
    refs.extend(
        {"kind": "company", "url": f"/company/company-{index}/"} for index in range(9)
    )
    first_result = {
        "url": "https://www.linkedin.com/search/results/people/",
        "sections": {"search_results": "RAW PROFILE SEARCH TEXT\nKey: Kubernetes"},
        "references": {"search_results": refs},
        "section_errors": {
            "search_results": {
                "error_type": "rate_limit",
                "error_message": "Try a narrower search",
                "runtime": {
                    "cookie_path": "/Users/operator/.linkedin-mcp/cookies.json",
                    "hostname": "private-host",
                },
            }
        },
    }
    second_result = {
        "url": "https://www.linkedin.com/search/results/people/?second=1",
        "sections": {"search_results": "SECOND RAW TEXT"},
        "references": {
            "search_results": [
                {
                    "kind": "person",
                    "url": "https://www.linkedin.com/in/alice?trk=search",
                    "text": "Alice Example",
                    "context": "Second source",
                }
            ]
        },
    }
    executor = FixtureExecutor([first_result, second_result])
    app = create_app(settings(tmp_path / "search.db"), queue_executor=executor)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        session_id = start_session(client)
        brief = client.post("/api/briefs", json=brief_payload(session_id)).json()
        request = {
            "session_id": session_id,
            "brief_id": brief["id"],
            "keywords": "platform engineer kubernetes",
            "location": "Chicago",
            "network": ["F", "S"],
            "current_company": "1115",
        }
        queued = client.post("/api/searches", json=request)
        assert queued.status_code == 202
        first_job = queued.json()["job_id"]
        assert wait_for_job(app, first_job).state == "done"
        assert executor.calls[0].model_dump(
            exclude={"kind", "search_run_id"}, exclude_none=True
        ) == {
            "keywords": "platform engineer kubernetes",
            "location": "Chicago",
            "network": ["F", "S"],
            "current_company": "1115",
        }
        with app.state.database.sessions() as db_session:
            run = db_session.scalar(
                select(SearchRun).where(SearchRun.job_id == first_job)
            )
            assert run is not None
            assert run.raw_response == {
                "content": [{"type": "text", "text": "fixture"}],
                "structuredContent": first_result,
                "isError": False,
                "futureProtocolField": {"preserved": True},
            }

        detail = client.get(f"/api/searches/{queued.json()['search_run_id']}")
        assert detail.status_code == 200
        assert detail.json()["person_reference_count"] == 6
        assert detail.json()["reference_count"] == 15
        assert detail.json()["raw_text"] == ("RAW PROFILE SEARCH TEXT\nKey: Kubernetes")
        assert detail.json()["status"] == "rate_limited"
        assert detail.json()["references"][0]["url"] == "/in/Alice/"
        assert detail.json()["references"][0]["context"] == (
            "Platform Engineer · Key: Kubernetes"
        )
        assert "runtime" not in detail.text
        assert ".linkedin-mcp" not in detail.text
        assert "private-host" not in detail.text

        client.post("/api/queue/resume")
        second = client.post(
            "/api/searches", json={**request, "keywords": "platform SRE"}
        ).json()
        assert wait_for_job(app, second["job_id"]).state == "done"
        candidates = client.get(
            "/api/candidates", params={"session_id": session_id}
        ).json()
        alice = next(item for item in candidates if item["username"] == "Alice")
        assert alice["source_count"] == 2
        assert alice["profile_urn"] is None
        assert alice["profile_urn_is_scored"] is False
        assert alice["sources"][0]["reference_context"] == (
            "Platform Engineer · Key: Kubernetes"
        )
        assert all(
            "not verified profile data" in row["notice"] for row in alice["sources"]
        )
        with app.state.database.sessions() as db_session:
            assert (
                db_session.scalar(
                    select(func.count(Candidate.id)).where(
                        Candidate.session_id == session_id
                    )
                )
                == 6
            )
            assert (
                db_session.scalar(select(func.count(CandidateSource.candidate_id))) == 7
            )


def test_company_lookup_is_queued_and_reports_found_or_absent(tmp_path) -> None:
    found = {
        "url": "https://www.linkedin.com/company/acme/about/",
        "sections": {"about": "Acme"},
        "references": {
            "about": [{"kind": "company_urn", "value": "12345", "text": "Acme"}]
        },
    }
    absent = {
        "url": "https://www.linkedin.com/company/small/about/",
        "sections": {"about": "Small"},
        "references": {"about": []},
    }
    executor = FixtureExecutor([found, absent])
    app = create_app(settings(tmp_path / "company.db"), queue_executor=executor)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        session_id = start_session(client)
        first = client.post(
            "/api/companies/urn-lookup",
            json={"session_id": session_id, "slug": "acme"},
        )
        assert first.status_code == 202
        assert wait_for_job(app, first.json()["job_id"]).state == "done"
        result = client.get(
            f"/api/companies/urn-lookups/{first.json()['lookup_id']}"
        ).json()
        assert result["candidates"] == [{"urn_id": "12345", "text": "Acme"}]

        second = client.post(
            "/api/companies/urn-lookup",
            json={"session_id": session_id, "slug": "small"},
        ).json()
        assert wait_for_job(app, second["job_id"]).state == "done"
        result = client.get(f"/api/companies/urn-lookups/{second['lookup_id']}").json()
        assert result["status"] == "not_exposed"
        assert "did not expose" in result["note"]


def test_committed_raw_response_reconciles_once_without_mcp_replay(tmp_path) -> None:
    result = {
        "url": "https://www.linkedin.com/search/results/people/",
        "sections": {"search_results": "Alice Example"},
        "references": {
            "search_results": [
                {
                    "kind": "person",
                    "url": "/in/Alice/",
                    "text": "Alice Example",
                }
            ]
        },
    }
    executor = FixtureExecutor([result])
    app = create_app(settings(tmp_path / "reconcile.db"), queue_executor=executor)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        session_id = start_session(client)
        brief = client.post("/api/briefs", json=brief_payload(session_id)).json()
        with app.state.database.sessions.begin() as db_session:
            control = db_session.get(QueueControl, 1)
            assert control is not None
            control.state = "paused"
            control.pause_reason = "RATE_LIMIT"
            control.operator_resume_required = True
        queued = client.post(
            "/api/searches",
            json={
                "session_id": session_id,
                "brief_id": brief["id"],
                "keywords": "platform engineer",
            },
        ).json()

        # Construct the durable state left by a process loss after the response
        # capture committed and before domain projection. All transitions obey
        # the same lifecycle constraints as the worker.
        with app.state.database.sessions.begin() as db_session:
            job = db_session.get(Job, queued["job_id"])
            assert job is not None
            now = datetime.now(UTC).isoformat()
            job.state = "running"
            job.attempts = 1
            job.started_at = now
            job.claim_token = "crashed-worker"
            db_session.flush()
            attempt = JobAttempt(
                id="crashed-attempt",
                job_id=job.id,
                attempt_number=1,
                worker_token="crashed-worker",
                started_at=now,
                response_received_at=now,
                finished_at=None,
                outcome="running",
                raw_response={
                    "content": [{"type": "text", "text": "fixture"}],
                    "structuredContent": result,
                    "isError": False,
                    "futureProtocolField": {"preserved": True},
                },
                raw_error=None,
                error_class=None,
                safe_error_message=None,
                retry_at=None,
            )
            db_session.add(attempt)
            db_session.flush()
            job.state = "interrupted"
            job.finished_at = now
            job.claim_token = None
            attempt.outcome = "interrupted"
            attempt.finished_at = now

        app.state.search_service.processor.reconcile()
        app.state.search_service.processor.reconcile()

        detail = client.get(f"/api/searches/{queued['search_run_id']}").json()
        assert detail["status"] == "interrupted"
        assert detail["reference_count"] == 1
        assert detail["person_reference_count"] == 1
        with app.state.database.sessions() as db_session:
            assert db_session.scalar(select(func.count(Candidate.id))) == 1
            assert (
                db_session.scalar(select(func.count(CandidateSource.candidate_id))) == 1
            )
        assert executor.calls == []


def test_retry_does_not_finalize_discovery_before_success(tmp_path) -> None:
    result = {
        "url": "https://www.linkedin.com/search/results/people/",
        "sections": {"search_results": "Alice Example"},
        "references": {
            "search_results": [
                {"kind": "person", "url": "/in/Alice/", "text": "Alice Example"}
            ]
        },
    }
    executor = TimeoutThenFixtureExecutor(result)
    app = create_app(
        settings(tmp_path / "retry-projection.db"), queue_executor=executor
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        session_id = start_session(client)
        brief = client.post("/api/briefs", json=brief_payload(session_id)).json()
        queued = client.post(
            "/api/searches",
            json={
                "session_id": session_id,
                "brief_id": brief["id"],
                "keywords": "platform engineer",
            },
        ).json()
        job = wait_for_job(app, queued["job_id"])
        assert job.state == "done"
        assert job.attempts == 2
        detail = client.get(f"/api/searches/{queued['search_run_id']}").json()
        assert detail["status"] == "ok"
        assert detail["person_reference_count"] == 1
        assert len(executor.calls) == 2


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("williamhgates", "williamhgates"),
        ("/in/williamhgates/", "williamhgates"),
        ("/mwlite/in/WilliamHGates/", "WilliamHGates"),
        ("https://www.linkedin.com/in/williamhgates", "williamhgates"),
        ("https://linkedin.com/in/williamhgates", "williamhgates"),
        ("http://www.linkedin.com/in/williamhgates", "williamhgates"),
        ("www.linkedin.com/in/williamhgates", "williamhgates"),
        ("linkedin.com/in/williamhgates", "williamhgates"),
        ("https://de.linkedin.com/in/williamhgates?trk=search", "williamhgates"),
        ("https://m.linkedin.com/in/williamhgates", "williamhgates"),
        ("https://touch.linkedin.com/in/williamhgates", "williamhgates"),
        (
            "https://www.linkedin.com/mwlite/profile/in/williamhgates",
            "williamhgates",
        ),
        ("https://www.linkedin.com/in/williamhgates#experience", "williamhgates"),
        (
            "https://www.linkedin.com/in/williamhgates/recent-activity/all/",
            "williamhgates",
        ),
        ("WilliamHGates", "WilliamHGates"),
        ("%D0%B0%D0%BD%D0%B4%D1%80%D0%B5%D0%B9", "андрей"),
    ],
)
def test_person_identifier_parity_accepts_server_reference_forms(
    value: str, expected: str
) -> None:
    assert normalize_person_reference(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "me",
        "ME",
        "%6d%65",
        ".",
        "..",
        "/in/../feed/",
        "/in/%252e%252e/",
        "https://www.linkedin.com/in/%2e%2e",
        "https://www.linkedin.com/in/%FF",
        "/company/acme/",
        "https://www.linkedin.com/school/rwth-aachen-university",
        "https://www.linkedin.com/feed/",
        "https://www.linkedin.com/sales/lead/ACwAA,NAME_SEARCH,abcd",
        "https://www.linkedin.com/pub/bill-gates/1/2a/3b",
        "https://lnkd.in/eXaMpLe1",
        "lnkd.in/eXaMpLe1",
        "https://evil-linkedin.com/in/williamhgates",
        "https://linkedin.com.example.test/in/williamhgates",
        "https://example.com/in/alice",
        "/in/a%2Fb/",
        "/in/%ZZ/",
        "felix%20foo",
        "williamhgates/../../feed",
        "bill gates",
        "in/williamhgates",
        "",
        "   ",
    ],
)
def test_person_identifier_parity_refuses_unsafe_forms(value: str) -> None:
    with pytest.raises(InvalidPersonReference):
        normalize_person_reference(value)
