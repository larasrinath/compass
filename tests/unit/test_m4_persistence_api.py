from __future__ import annotations

import ast
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from linkedin_dashboard.api._filters import sanitize_for_frontend
from linkedin_dashboard.db.models import (
    BriefCredential,
    CandidateScore,
    Evidence,
    EvidenceSetRecord,
    Job,
    PhaseGateEvidence,
    ProfileSection,
    ScoreClaim,
    ScoreInputSection,
    ScoreSignal,
)
from linkedin_dashboard.main import create_app
from linkedin_dashboard.queue.jobs import JobPayload
from linkedin_dashboard.queue.worker import ProgressReporter, RawCapture
from linkedin_dashboard.settings import Settings
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError


class FixtureExecutor:
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
        await capture_raw(
            {
                "content": [{"type": "text", "text": "fixture"}],
                "structuredContent": result,
                "isError": False,
            },
            None,
        )
        return result


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


def _settings(path: Path) -> Settings:
    return Settings(
        db_path=path,
        llm_provider="null",
        send_enabled=False,
        inter_call_delay_seconds=0,
    )


def _search_result() -> dict[str, Any]:
    return {
        "url": "https://www.linkedin.com/search/results/people/",
        "sections": {"search_results": "Ada Example"},
        "references": {
            "search_results": [
                {"kind": "person", "url": "/in/ada/", "text": "Ada Example"}
            ]
        },
    }


def _profile_result(**sections: str) -> dict[str, Any]:
    return {
        "url": "https://www.linkedin.com/in/ada/",
        "sections": sections,
    }


def _brief(session_id: str) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "job_description": "Build a platform.",
        "required_skills": [
            {"term": f"skill{index}", "aliases": []} for index in range(10)
        ],
        "optional_skills": [],
        "target_titles": [],
        "location": "",
        "industries": [],
        "positive_keywords": [],
        "negative_keywords": [],
        "message_tone": "Direct",
    }


def test_m4_score_gate_and_exact_evidence_lifecycle(tmp_path: Path) -> None:
    skills = " ".join(f"skill{index}" for index in range(10))
    executor = FixtureExecutor(
        [
            _search_result(),
            _profile_result(
                main_profile="Ada Example\nPlatform Engineer",
            ),
            _profile_result(main_profile="Ada Example", skills=skills),
            _profile_result(skills=f"{skills} rollback-marker"),
            _profile_result(skills=f"{skills} newer-marker"),
            {
                "url": "https://www.linkedin.com/in/ada/",
                "sections": {},
                "section_errors": {
                    "experience": {
                        "error_type": "unparseable",
                        "error_message": "exact later fetch error",
                    }
                },
            },
        ]
    )
    app = create_app(_settings(tmp_path / "m4.db"), queue_executor=executor)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        created = client.post("/api/session", json={"label": "M4"})
        session_id = created.json()["id"]
        brief = client.post("/api/briefs", json=_brief(session_id))
        assert brief.status_code == 201
        search = client.post(
            "/api/searches",
            json={
                "session_id": session_id,
                "brief_id": brief.json()["id"],
                "keywords": "platform",
            },
        )
        assert _wait(app, search.json()["job_id"]).state == "done"
        pool = client.get("/api/candidate-pool", params={"session_id": session_id})
        candidate_id = pool.json()[0]["id"]

        pre_gate = client.get(f"/api/candidates/{candidate_id}")
        assert pre_gate.status_code == 200
        assert "score" not in pre_gate.json()
        assert (
            client.get("/api/candidates", params={"session_id": session_id}).status_code
            == 409
        )

        gate_a = client.post(
            "/api/session/gates/A", json={"note": "Candidate pool reviewed."}
        )
        assert gate_a.status_code == 201

        first = client.post(f"/api/candidates/{candidate_id}/enrich", json={})
        assert _wait(app, first.json()["job_id"]).state == "done"
        second = client.post(
            f"/api/candidates/{candidate_id}/enrich",
            json={"sections": ["skills"]},
        )
        assert _wait(app, second.json()["job_id"]).state == "done"

        detail = client.get(f"/api/candidates/{candidate_id}").json()
        assert detail["score"]["calculation_status"] == "scored"
        assert detail["score"]["score_id"]
        signal = next(row for row in detail["signals"] if row["signal_id"] == "S-1")
        evidence_ids = [
            item["id"]
            for claim in signal["claims"]
            for item in claim["evidence"]
            if item["availability"]["state"] == "available"
        ]
        assert len(set(evidence_ids)) == 10
        section = client.get(f"/api/candidates/{candidate_id}/sections/skills").json()
        assert set(evidence_ids).issubset({item["id"] for item in section["spans"]})

        assert (
            client.post(
                "/api/session/gates/B",
                json={"evidence_ids": [evidence_ids[0]] * 10},
            ).status_code
            == 422
        )

        calls_before_rescore = len(executor.calls)
        rescore = client.post(f"/api/candidates/{candidate_id}/rescore")
        assert rescore.status_code == 200
        assert len(executor.calls) == calls_before_rescore
        assert rescore.json()["score_id"] == detail["score"]["score_id"]
        config = client.get("/api/weights").json()
        changed_weights = dict(config["weights"])
        changed_weights["S-1"] += 1
        changed = client.put(
            "/api/weights/current",
            json={
                "expected_version": config["version"],
                "weights": changed_weights,
                "metro_region_equivalences": {},
            },
        )
        assert changed.status_code == 200
        stale = client.post(
            "/api/session/gates/B",
            json={"evidence_ids": evidence_ids, "note": "stale"},
        )
        assert stale.status_code == 409

        refreshed = client.get(f"/api/candidates/{candidate_id}").json()
        current_ids = [
            item["id"]
            for signal in refreshed["signals"]
            for claim in signal["claims"]
            for item in claim["evidence"]
            if item["availability"]["state"] == "available"
        ]
        score_before_failure = refreshed["score"]["score_id"]
        with app.state.database.sessions() as session:
            section_count = session.scalar(
                select(func.count(ProfileSection.id)).where(
                    ProfileSection.candidate_id == candidate_id
                )
            )
        original_rescore = app.state.scoring_service.rescore_candidate_in_session
        app.state.scoring_service.rescore_candidate_in_session = (
            lambda _session, _candidate: (_ for _ in ()).throw(
                ValueError("forced scoring rollback")
            )
        )
        failed = client.post(
            f"/api/candidates/{candidate_id}/enrich",
            json={"sections": ["skills"]},
        ).json()
        assert _wait(app, failed["job_id"]).state == "failed"
        with app.state.database.sessions() as session:
            assert (
                session.scalar(
                    select(func.count(ProfileSection.id)).where(
                        ProfileSection.candidate_id == candidate_id
                    )
                )
                == section_count
            )
        assert (
            client.get(f"/api/candidates/{candidate_id}").json()["score"]["score_id"]
            == score_before_failure
        )

        app.state.scoring_service.rescore_candidate_in_session = (
            lambda _session, _candidate: None
        )
        unscored = client.post(
            f"/api/candidates/{candidate_id}/enrich",
            json={"sections": ["skills"]},
        ).json()
        assert _wait(app, unscored["job_id"]).state == "done"
        assert (
            client.post(
                "/api/session/gates/B",
                json={"evidence_ids": current_ids[:10], "note": "stale profile"},
            ).status_code
            == 409
        )
        app.state.scoring_service.rescore_candidate_in_session = original_rescore
        assert client.post(f"/api/candidates/{candidate_id}/rescore").status_code == 200
        refreshed = client.get(f"/api/candidates/{candidate_id}").json()
        current_ids = [
            item["id"]
            for signal in refreshed["signals"]
            for claim in signal["claims"]
            for item in claim["evidence"]
            if item["availability"]["state"] == "available"
        ]
        score_id = refreshed["score"]["score_id"]
        fingerprint = refreshed["score"]["input_fingerprint"]
        with app.state.database.sessions() as session:
            persisted = session.get(CandidateScore, score_id)
            assert persisted is not None
            sections = persisted.source_snapshot["profile_snapshot"]["sections"]
            experience_snapshot = next(
                item for item in sections if item["name"] == "experience"
            )
            assert experience_snapshot == {
                "id": "missing:experience",
                "name": "experience",
                "state": "missing",
                "content_sha256": None,
                "missing_reason": "not_requested",
                "section_error_id": None,
            }
        app.state.scoring_service.rescore_candidate_in_session = (
            lambda _session, _candidate: None
        )
        error_only = client.post(
            f"/api/candidates/{candidate_id}/enrich",
            json={"sections": ["experience"]},
        ).json()
        assert _wait(app, error_only["job_id"]).state == "done"
        manifest = [
            {
                "evidence_id": evidence_id,
                "score_id": score_id,
                "input_fingerprint": fingerprint,
            }
            for evidence_id in current_ids[:10]
        ]
        for recursive in ("ON", "OFF"):
            with sqlite3.connect(app.state.database.path) as connection:
                connection.execute(f"PRAGMA recursive_triggers={recursive}")
                with pytest.raises(
                    sqlite3.IntegrityError,
                    match="ten current exact evidence spans",
                ):
                    connection.execute(
                        "INSERT INTO phase_gate "
                        "(id,session_id,gate,accepted_at,accepted_note,"
                        "evidence_manifest) VALUES "
                        "(?,?,'B','now','raw stale error',?)",
                        (
                            f"raw-stale-error-{recursive}",
                            session_id,
                            json.dumps(manifest),
                        ),
                    )
        assert (
            client.post(
                "/api/session/gates/B",
                json={"evidence_ids": current_ids[:10], "note": "stale error"},
            ).status_code
            == 409
        )
        app.state.scoring_service.rescore_candidate_in_session = original_rescore
        assert client.post(f"/api/candidates/{candidate_id}/rescore").status_code == 200
        refreshed = client.get(f"/api/candidates/{candidate_id}").json()
        current_ids = [
            item["id"]
            for signal in refreshed["signals"]
            for claim in signal["claims"]
            for item in claim["evidence"]
            if item["availability"]["state"] == "available"
        ]
        gate_b = client.post(
            "/api/session/gates/B",
            json={"evidence_ids": current_ids[:10], "note": "Verified exactly."},
        )
        assert gate_b.status_code == 201
        assert len(gate_b.json()["evidence_ids"]) == 10
        with pytest.raises(IntegrityError, match="phase gate evidence is immutable"):
            with app.state.database.sessions.begin() as session:
                session.execute(
                    update(PhaseGateEvidence)
                    .where(PhaseGateEvidence.evidence_id == current_ids[0])
                    .values(input_fingerprint="0" * 64)
                )
        with pytest.raises(IntegrityError, match="phase gate evidence is append-only"):
            with app.state.database.sessions.begin() as session:
                session.execute(
                    delete(PhaseGateEvidence).where(
                        PhaseGateEvidence.evidence_id == current_ids[0]
                    )
                )

        with app.state.database.sessions() as session:
            score_count = session.scalar(
                select(func.count(CandidateScore.id)).where(
                    CandidateScore.candidate_id == candidate_id
                )
            )
            current_count = session.scalar(
                select(func.count(CandidateScore.id)).where(
                    CandidateScore.candidate_id == candidate_id,
                    CandidateScore.is_current.is_(True),
                )
            )
            evidence = session.get(Evidence, current_ids[0])
            assert evidence is not None
            source_score = session.scalar(
                select(CandidateScore)
                .join(ScoreSignal, ScoreSignal.score_id == CandidateScore.id)
                .where(ScoreSignal.id == evidence.score_signal_id)
            )
            assert source_score is not None
            finalized_score_ids = [
                source_score.id,
                refreshed["score"]["score_id"],
            ]
            assert "newer-marker" not in str(source_score.source_snapshot)
            assert "rollback-marker" not in str(source_score.source_snapshot)
            staged_score = CandidateScore(
                id="staged-score",
                candidate_id=source_score.candidate_id,
                brief_id=source_score.brief_id,
                weights_version=source_score.weights_version,
                scoring_config_id=source_score.scoring_config_id,
                stage=source_score.stage,
                score=1,
                score_lower=1,
                score_upper=1,
                confidence=1,
                confidence_band="high",
                calculation_status="scored",
                active_signal_count=1,
                all_inert_attested=False,
                input_fingerprint="f" * 64,
                source_snapshot={},
                computed_at="2026-09-04T00:00:00+00:00",
                superseded_at=None,
                is_current=False,
            )
            staged_signal = ScoreSignal(
                id="staged-signal",
                score_id=staged_score.id,
                signal_id="S-1",
                weight=1,
                verdict="matched",
                rollup="matched",
                raw_subscore=1,
                contribution=1,
                availability=1,
                note=None,
            )
            staged_set = EvidenceSetRecord(
                id="staged-set",
                candidate_id=source_score.candidate_id,
                score_signal_id=staged_signal.id,
            )
            staged_source = ScoreInputSection(
                score_id=staged_score.id,
                profile_section_id=evidence.profile_section_id,
                content_sha256=evidence.content_sha256,
            )
            late = Evidence(
                id="late-evidence",
                score_signal_id=evidence.score_signal_id,
                evidence_set_id=evidence.evidence_set_id,
                parsed_field_id=None,
                section_name=evidence.section_name,
                profile_section_id=evidence.profile_section_id,
                content_sha256=evidence.content_sha256,
                span_start=evidence.span_start,
                span_end=evidence.span_end,
                snippet=evidence.snippet,
                matcher=evidence.matcher,
                matched_term=evidence.matched_term,
                polarity=evidence.polarity,
                purged_at=None,
            )
            bad = Evidence(
                id="bad-evidence",
                score_signal_id=staged_signal.id,
                evidence_set_id=staged_set.id,
                parsed_field_id=None,
                section_name=evidence.section_name,
                profile_section_id=evidence.profile_section_id,
                content_sha256=evidence.content_sha256,
                span_start=evidence.span_start,
                span_end=evidence.span_end,
                snippet="not the stored span",
                matcher="exact",
                matched_term=evidence.matched_term,
                polarity=evidence.polarity,
                purged_at=None,
            )
        assert score_count >= 4
        assert current_count == 1
        for index, finalized_score_id in enumerate(finalized_score_ids):
            with pytest.raises(
                IntegrityError, match="only staged M4 scores accept new signals"
            ):
                with app.state.database.sessions.begin() as session:
                    session.add(
                        ScoreSignal(
                            id=f"late-signal-{index}",
                            score_id=finalized_score_id,
                            signal_id="S-2",
                            weight=0,
                            verdict="unknown",
                            rollup=None,
                            raw_subscore=0,
                            contribution=0,
                            availability=0,
                            note=None,
                        )
                    )
        with pytest.raises(IntegrityError, match="score snapshot is finalized"):
            with app.state.database.sessions.begin() as session:
                session.add(late)
        with app.state.database.sessions.begin() as session:
            session.add(staged_score)
            session.flush()
            session.add(staged_signal)
            session.flush()
            session.add_all([staged_set, staged_source])
        with pytest.raises(IntegrityError, match="exact same-candidate span"):
            with app.state.database.sessions.begin() as session:
                session.add(bad)
        with pytest.raises(IntegrityError, match="provenance is incomplete"):
            with app.state.database.sessions.begin() as session:
                session.add(
                    ScoreClaim(
                        id="cross-owner-claim",
                        score_signal_id=staged_signal.id,
                        claim_key="S-1:skill0",
                        display_term="skill0",
                        verdict="matched",
                        evidence_set_id=evidence.evidence_set_id,
                        coverage_set_id=None,
                        missing_set_id=None,
                    )
                )


def test_weight_versioning_and_all_inert_nullable_score(tmp_path: Path) -> None:
    app = create_app(
        _settings(tmp_path / "weights.db"),
        queue_executor=FixtureExecutor([]),
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        session_id = client.post("/api/session", json={"label": "inert"}).json()["id"]
        payload = _brief(session_id)
        payload["required_skills"] = []
        payload["positive_keywords"] = ["platform"]
        assert client.post("/api/briefs", json=payload).status_code == 201
        with app.state.database.sessions.begin() as session:
            from linkedin_dashboard.db.models import Candidate

            candidate = Candidate(
                session_id=session_id,
                username="inert",
                profile_url="https://www.linkedin.com/in/inert",
                display_name="Inert",
                first_seen_at="2026-09-04T00:00:00+00:00",
                stage="discovered",
                retrieval_status="pending",
            )
            session.add(candidate)
            session.flush()
            candidate_id = candidate.id

        score = client.post(f"/api/candidates/{candidate_id}/rescore")
        assert score.status_code == 200
        assert score.json()["score"] is None
        assert score.json()["confidence"] == 0
        assert score.json()["all_inert_attested"] is True

        config = client.get("/api/weights").json()
        zero = {key: 0 for key in config["weights"]}
        updated = client.put(
            "/api/weights/current",
            json={
                "expected_version": config["version"],
                "weights": zero,
                "metro_region_equivalences": {},
            },
        )
        assert updated.status_code == 200
        stale = client.put(
            "/api/weights/current",
            json={
                "expected_version": config["version"],
                "weights": zero,
                "metro_region_equivalences": {},
            },
        )
        assert stale.status_code == 409
        active = dict(payload)
        active["required_skills"] = [{"term": "Python", "aliases": []}]
        rejected = client.put("/api/briefs/current", json=active)
        assert rejected.status_code == 422
        current = client.get("/api/briefs/current", params={"session_id": session_id})
        assert current.json()["version"] == 1


def test_frozen_brief_omissions_preserve_m4_fields(tmp_path: Path) -> None:
    app = create_app(
        _settings(tmp_path / "brief-omission.db"),
        queue_executor=FixtureExecutor([]),
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        session_id = client.post("/api/session", json={"label": "brief"}).json()["id"]
        payload = _brief(session_id)
        payload["required_experience_months"] = 48
        payload["required_credentials"] = [
            {"term": "AWS Professional", "aliases": ["AWS Pro"]}
        ]
        assert client.post("/api/briefs", json=payload).status_code == 201

        frozen_frontend_payload = _brief(session_id)
        frozen_frontend_payload["message_tone"] = "Warm"
        updated = client.put("/api/briefs/current", json=frozen_frontend_payload)
        assert updated.status_code == 200
        assert updated.json()["required_experience_months"] == 48
        assert updated.json()["required_credentials"] == [
            {"term": "AWS Professional", "aliases": ["AWS Pro"]}
        ]
        with app.state.database.sessions() as session:
            credentials = list(
                session.scalars(
                    select(BriefCredential).where(
                        BriefCredential.brief_id == updated.json()["id"]
                    )
                )
            )
        assert [(row.term, row.aliases) for row in credentials] == [
            ("AWS Professional", ["AWS Pro"])
        ]


def test_brief_credential_allowlist_is_structural_and_narrow() -> None:
    payload = {
        "required_credentials": [
            {
                "term": "AWS Professional",
                "aliases": ["AWS Pro"],
                "proxy_credentials": "secret",
                "password": "secret",
                "runtime": {"cookie_path": "/private/profile/cookies.json"},
            }
        ],
        "signals": {
            "claims": {
                "evidence": {
                    "required_credentials": ["must remain hidden here"],
                    "authorization": "Bearer secret",
                }
            }
        },
    }
    assert "required_credentials" not in sanitize_for_frontend(payload)
    safe = sanitize_for_frontend(payload, _preserve_brief_domain=True)
    assert safe["required_credentials"] == [
        {"term": "AWS Professional", "aliases": ["AWS Pro"]}
    ]
    assert "required_credentials" not in safe["signals"]["claims"]["evidence"]
    assert "authorization" not in safe["signals"]["claims"]["evidence"]
    assert "secret" not in str(safe).casefold()
    assert "/private" not in str(safe).casefold()


def test_scoring_persistence_stays_outside_pure_kernel() -> None:
    services = Path(__file__).parents[2] / "backend/linkedin_dashboard/services"
    kernel = services / "scoring"
    boundary_paths = (
        services / "scoring_persist.py",
        services / "scoring_service.py",
    )
    for path in kernel.rglob("*.py"):
        imported = {
            node.module or ""
            for node in ast.walk(ast.parse(path.read_text()))
            if isinstance(node, ast.ImportFrom)
        }
        assert "sqlalchemy" not in imported
        assert "linkedin_dashboard.db.models" not in imported
    for path in boundary_paths:
        source = path.read_text()
        assert "linkedin_dashboard.db.models" in source
        assert "linkedin_dashboard.services.scoring" in source
        assert "linkedin_dashboard.mcp" not in source
