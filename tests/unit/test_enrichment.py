from __future__ import annotations

import random
import string
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from linkedin_dashboard.api._filters import redact_provenance_text
from linkedin_dashboard.db.models import (
    Candidate,
    DashboardSession,
    Job,
    ParsedField,
    ProfileFetch,
    ProfileSection,
    SectionError,
)
from linkedin_dashboard.llm import NullProvider
from linkedin_dashboard.main import create_app
from linkedin_dashboard.parsing import PARSERS
from linkedin_dashboard.parsing.spans import VerifiedSpan
from linkedin_dashboard.parsing.verify import SpanProposal, verify_proposal
from linkedin_dashboard.queue.jobs import JobPayload, PersonProfilePayload
from linkedin_dashboard.queue.worker import ProgressReporter, RawCapture
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
            assert session.scalar(select(func.count(ProfileSection.id))) == 2
            error = session.scalar(
                select(SectionError).where(SectionError.fetch_id.is_not(None))
            )
            assert error is not None
            assert "runtime" not in (error.extra or {})
            dashboard_session = session.get(DashboardSession, child.session_id)
            assert dashboard_session is not None and dashboard_session.nav_used == 3
        assert client.post("/api/queue/resume").status_code == 200
        assert _wait(app, child.id).state == "done"
        with app.state.database.sessions() as session:
            dashboard_session = session.get(DashboardSession, child.session_id)
            assert dashboard_session is not None and dashboard_session.nav_used == 6


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
        assert "contracts have drifted" in caplog.text
        with app.state.database.sessions() as session:
            error = session.scalar(
                select(SectionError).where(
                    SectionError.fetch_id.is_not(None),
                    SectionError.error_type == "unknown_section",
                )
            )
            assert error is not None and error.section_name == "future_section"


def test_stage_two_cap_validation_and_upstream_order(tmp_path) -> None:
    app = create_app(
        _settings(tmp_path / "validation.db"), queue_executor=ProfileExecutor([])
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
        queued = client.post(
            f"/api/candidates/{candidate_id}/enrich",
            json={"sections": ["projects", "education", "skills"]},
        )
        assert queued.status_code == 202
        with app.state.database.sessions() as session:
            job = session.get(Job, queued.json()["job_id"])
            assert job is not None
            assert job.payload["sections"] == ["education", "skills", "projects"]


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
