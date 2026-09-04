from __future__ import annotations

import ast
import hashlib
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from linkedin_dashboard.api.scoring import _sort_ranked_records
from linkedin_dashboard.db.migrations import v0023_m4_scoring
from linkedin_dashboard.db.models import (
    BriefCredential,
    Candidate,
    CandidateScore,
    DashboardSession,
    Job,
    MissingSetRecord,
    ProfileSection,
    RoleBrief,
    ScoreInputSection,
    ScoreSignal,
    ScoringConfig,
    SectionError,
    SignalMissingSection,
)
from linkedin_dashboard.db.unicode_identity import register_sqlite_unicode_casefold
from linkedin_dashboard.main import create_app
from linkedin_dashboard.queue.jobs import JobPayload
from linkedin_dashboard.queue.worker import ProgressReporter, RawCapture
from linkedin_dashboard.services.scoring_persist import _date_range_months
from linkedin_dashboard.services.scoring_service import ConfigVersionConflict
from linkedin_dashboard.settings import Settings
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError


class PoisonExecutor:
    calls = 0

    async def execute(
        self,
        payload: JobPayload,
        capture_raw: RawCapture,
        report_progress: ProgressReporter,
    ) -> dict[str, Any]:
        del payload, capture_raw, report_progress
        self.calls += 1
        raise AssertionError("a local scoring boundary made a network call")


class SequenceExecutor:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)

    async def execute(
        self,
        payload: JobPayload,
        capture_raw: RawCapture,
        report_progress: ProgressReporter,
    ) -> dict[str, Any]:
        del payload
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


def _brief(session_id: str, **changes: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "session_id": session_id,
        "job_description": "Platform engineer",
        "required_skills": [{"term": "Python", "aliases": []}],
        "optional_skills": [],
        "required_experience_months": None,
        "target_titles": [],
        "location": "",
        "industries": [],
        "required_credentials": [{"term": "AWS Professional", "aliases": ["AWS Pro"]}],
        "positive_keywords": [],
        "negative_keywords": [],
        "message_tone": "Direct",
    }
    payload.update(changes)
    return payload


def _seed_current_score(app: Any, client: TestClient) -> tuple[str, str, str]:
    session_id = client.post("/api/session", json={"label": "guards"}).json()["id"]
    brief = client.post("/api/briefs", json=_brief(session_id))
    assert brief.status_code == 201
    with app.state.database.sessions.begin() as session:
        candidate = Candidate(
            session_id=session_id,
            username="guarded",
            profile_url="https://www.linkedin.com/in/guarded",
            display_name="Guarded",
            first_seen_at="2026-09-04T00:00:00+00:00",
            stage="discovered",
            retrieval_status="pending",
        )
        session.add(candidate)
        session.flush()
        candidate_id = candidate.id
    response = client.post(f"/api/candidates/{candidate_id}/rescore")
    assert response.status_code == 200
    return session_id, brief.json()["id"], response.json()["score_id"]


def test_raw_current_insert_and_sealed_brief_mutations_fail(tmp_path: Path) -> None:
    executor = PoisonExecutor()
    app = create_app(_settings(tmp_path / "raw-guards.db"), queue_executor=executor)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        session_id, brief_id, score_id = _seed_current_score(app, client)
        with app.state.database.sessions.begin() as session:
            forged_candidate = Candidate(
                id="forged-candidate",
                session_id=session_id,
                username="forged",
                profile_url="https://www.linkedin.com/in/forged",
                display_name="Forged",
                first_seen_at="2026-09-04T00:00:00+00:00",
                stage="discovered",
                retrieval_status="pending",
            )
            session.add(forged_candidate)
        with pytest.raises(IntegrityError, match="inserted as staged"):
            with app.state.database.sessions.begin() as session:
                session.execute(
                    text(
                        "INSERT INTO score "
                        "SELECT 'forged-current','forged-candidate',brief_id,"
                        "weights_version,"
                        "scoring_config_id,stage,score,score_lower,score_upper,confidence,"
                        "confidence_band,calculation_status,active_signal_count,"
                        "all_inert_attested,'f' || substr(input_fingerprint,2),"
                        "source_snapshot,computed_at,"
                        "superseded_at,1 FROM score WHERE id=:score"
                    ),
                    {"score": score_id},
                )
        with pytest.raises(IntegrityError, match=r"scoring config|score roots"):
            with app.state.database.sessions.begin() as session:
                session.execute(
                    text(
                        "INSERT INTO score "
                        "SELECT 'forged-null-config','forged-candidate',brief_id,"
                        "weights_version,NULL,stage,score,score_lower,score_upper,"
                        "confidence,confidence_band,calculation_status,"
                        "active_signal_count,all_inert_attested,"
                        "'d' || substr(input_fingerprint,2),source_snapshot,"
                        "computed_at,NULL,0 FROM score WHERE id=:score"
                    ),
                    {"score": score_id},
                )
        with pytest.raises(IntegrityError, match="incomplete or inconsistent"):
            with app.state.database.sessions.begin() as session:
                session.execute(
                    text(
                        "INSERT INTO score "
                        "SELECT 'forged-staged','forged-candidate',brief_id,"
                        "weights_version,scoring_config_id,stage,score,score_lower,"
                        "score_upper,confidence,confidence_band,calculation_status,"
                        "active_signal_count,all_inert_attested,"
                        "'e' || substr(input_fingerprint,2),'{}',computed_at,NULL,0 "
                        "FROM score WHERE id=:score"
                    ),
                    {"score": score_id},
                )
                session.execute(
                    text("UPDATE score SET is_current=1 WHERE id='forged-staged'")
                )
        with pytest.raises(IntegrityError, match="role brief versions are append-only"):
            with app.state.database.sessions.begin() as session:
                session.execute(
                    text(
                        "UPDATE role_brief SET required_experience_months=24 "
                        "WHERE id=:brief"
                    ),
                    {"brief": brief_id},
                )
        with pytest.raises(IntegrityError, match="sealed role brief credentials"):
            with app.state.database.sessions.begin() as session:
                session.add(
                    BriefCredential(
                        id="late-credential",
                        brief_id=brief_id,
                        term="Late",
                        term_key="late",
                        aliases=[],
                        position=99,
                    )
                )
        assert executor.calls == 0


@pytest.mark.parametrize("recursive", ("ON", "OFF"))
def test_or_replace_cannot_rewrite_config_or_credential(
    tmp_path: Path, recursive: str
) -> None:
    app = create_app(
        _settings(tmp_path / f"replace-{recursive}.db"), queue_executor=PoisonExecutor()
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        session_id, brief_id, _ = _seed_current_score(app, client)
        with app.state.database.sessions() as session:
            credential = session.scalar(
                select(BriefCredential).where(BriefCredential.brief_id == brief_id)
            )
            assert credential is not None
            credential_id = credential.id
        with app.state.database.sessions() as session:
            current = session.scalar(
                select(ScoringConfig).where(
                    ScoringConfig.session_id == session_id,
                    ScoringConfig.superseded_at.is_(None),
                )
            )
            assert current is not None
            config_version = current.version
            config_weights = json.dumps(current.weights)
        with sqlite3.connect(app.state.database.path) as connection:
            register_sqlite_unicode_casefold(connection)
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(f"PRAGMA recursive_triggers={recursive}")
            with pytest.raises(sqlite3.IntegrityError, match="already exists"):
                connection.execute(
                    "INSERT OR REPLACE INTO brief_credential "
                    "SELECT id,brief_id,'Poison',term_key,aliases,position "
                    "FROM brief_credential WHERE id=?",
                    (credential_id,),
                )
            with pytest.raises(sqlite3.IntegrityError, match="version already exists"):
                connection.execute(
                    "INSERT OR REPLACE INTO scoring_config "
                    "(id,session_id,version,created_at,weights,"
                    "metro_region_equivalences,superseded_at) VALUES "
                    "('replacement',?,?, 'now',?,'{}',NULL)",
                    (session_id, config_version + 1, config_weights),
                )
            with pytest.raises(sqlite3.IntegrityError, match="version already exists"):
                connection.execute(
                    "INSERT OR REPLACE INTO role_brief SELECT * FROM role_brief "
                    "WHERE id=?",
                    (brief_id,),
                )
            with pytest.raises(sqlite3.IntegrityError, match="version already exists"):
                connection.execute(
                    "INSERT OR REPLACE INTO role_brief "
                    "(id,session_id,version,created_at,sealed_at,superseded_at,"
                    "job_description,target_titles,location,industries,"
                    "positive_keywords,negative_keywords,message_tone,"
                    "required_experience_months,weights_version) "
                    "SELECT 'replacement-brief',session_id,version+1,'now',"
                    "'now',NULL,job_description,target_titles,location,industries,"
                    "positive_keywords,negative_keywords,message_tone,"
                    "required_experience_months,weights_version FROM role_brief "
                    "WHERE id=?",
                    (brief_id,),
                )
            assert connection.execute(
                "SELECT count(*) FROM brief_credential WHERE brief_id=?",
                (brief_id,),
            ).fetchone() == (1,)


def test_validation_is_strict_bounded_and_never_echoes_input(tmp_path: Path) -> None:
    app = create_app(
        _settings(tmp_path / "validation.db"), queue_executor=PoisonExecutor()
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        session_id = client.post("/api/session", json={"label": "validation"}).json()[
            "id"
        ]
        secret_inputs = (
            "password=do-not-echo",
            "token=do-not-echo",
            "proxy_credential=do-not-echo",
            "Authorization: Bearer do-not-echo",
            "cookie=session-do-not-echo",
            "runtime=/private/do-not-echo",
            "host=private.internal",
            "/Users/operator/.linkedin-mcp/profile",
        )
        for secret in secret_inputs:
            rejected = client.post(
                "/api/briefs",
                json=_brief(session_id, required_experience_months=secret),
            )
            assert rejected.status_code == 422
            assert secret not in rejected.text
            assert '"input"' not in rejected.text
        assert (
            client.post(
                "/api/briefs",
                json=_brief(session_id, required_experience_months=True),
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/briefs",
                content=b"{" + b'"padding":"' + b"x" * (256 * 1024) + b'"}',
                headers={"content-type": "application/json"},
            ).status_code
            == 413
        )
        compatibility_age = "\u24d0\u24d6\u24d4"
        for field in (
            "required_skills",
            "optional_skills",
            "target_titles",
            "industries",
            "required_credentials",
        ):
            assert (
                client.post(
                    "/api/briefs",
                    json=_brief(
                        session_id,
                        **{field: [{"term": compatibility_age, "aliases": []}]},
                    ),
                ).status_code
                == 422
            )
        for field in ("positive_keywords", "negative_keywords"):
            assert (
                client.post(
                    "/api/briefs",
                    json=_brief(session_id, **{field: [compatibility_age]}),
                ).status_code
                == 422
            )
        for location in ("\uff21\uff27\uff25", compatibility_age):
            assert (
                client.post(
                    "/api/briefs",
                    json=_brief(session_id, location=location),
                ).status_code
                == 422
            )
        assert (
            client.post(
                "/api/briefs",
                json=_brief(
                    session_id,
                    required_credentials=[
                        {"term": "safe", "aliases": [compatibility_age]}
                    ],
                ),
            ).status_code
            == 422
        )
        assert client.post("/api/briefs", json=_brief(session_id)).status_code == 201
        before = client.get("/api/weights").json()
        boolean_weights = dict(before["weights"])
        boolean_weights["S-1"] = True
        assert (
            client.put(
                "/api/weights/current",
                json={
                    "expected_version": before["version"],
                    "weights": boolean_weights,
                    "metro_region_equivalences": {},
                },
            ).status_code
            == 422
        )
        assert (
            client.put(
                "/api/weights/current",
                json={
                    "expected_version": before["version"],
                    "weights": before["weights"],
                    "metro_region_equivalences": {"region": ["\uff41\uff47\uff45"]},
                },
            ).status_code
            == 422
        )
        assert client.get("/api/weights").json()["version"] == before["version"]


def test_concurrent_config_cas_has_one_winner(tmp_path: Path) -> None:
    app = create_app(
        _settings(tmp_path / "concurrent.db"), queue_executor=PoisonExecutor()
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        session_id, _, _ = _seed_current_score(app, client)
        before = client.get("/api/weights").json()

        def update_once() -> object:
            try:
                return app.state.scoring_service.update_config(
                    session_id=session_id,
                    expected_version=before["version"],
                    weights=before["weights"],
                    metro_region_equivalences={},
                )
            except ConfigVersionConflict as error:
                return error

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: update_once(), range(2)))
        assert sum(isinstance(item, dict) for item in results) == 1
        assert sum(isinstance(item, ConfigVersionConflict) for item in results) == 1
        assert client.get("/api/weights").json()["version"] == "2"


def test_date_ranges_are_deterministic_and_ambiguous_ranges_stay_unknown() -> None:
    as_of = date(2026, 9, 4)
    assert _date_range_months("Jan 2018 - Dec 2023", as_of=as_of) == 72
    assert _date_range_months("2024-01 to 2024-12", as_of=as_of) == 12
    assert _date_range_months("Jan 2026 \u2013 Present", as_of=as_of) == 9
    assert _date_range_months("2020 - 2021", as_of=as_of) is None
    assert _date_range_months("13/02/2020 - 14/03/2021", as_of=as_of) is None


def test_rank_sort_modes_are_stable_and_null_last() -> None:
    records = [
        {
            "id": "b",
            "score": 80.0,
            "confidence": 0.8,
            "display_name": "Zed",
            "username": "zed",
        },
        {
            "id": "a",
            "score": 80.0,
            "confidence": 0.8,
            "display_name": "Amy",
            "username": "amy",
        },
        {
            "id": "c",
            "score": None,
            "confidence": 1.0,
            "display_name": None,
            "username": "null",
        },
    ]
    assert [row["id"] for row in _sort_ranked_records(records, "score_desc")] == [
        "a",
        "b",
        "c",
    ]
    assert [row["id"] for row in _sort_ranked_records(records, "confidence_desc")] == [
        "a",
        "b",
        "c",
    ]
    assert [row["id"] for row in _sort_ranked_records(records, "name_asc")] == [
        "a",
        "b",
        "c",
    ]


def test_v22_gate_b_preflight_is_actionable_and_rollback_safe(tmp_path: Path) -> None:
    path = tmp_path / "legacy-gate.db"
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE phase_gate(id TEXT PRIMARY KEY,gate TEXT)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE role_brief(session_id TEXT,superseded_at TEXT)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE score(candidate_id TEXT,is_current INTEGER)"
        )
        connection.exec_driver_sql("INSERT INTO phase_gate VALUES('legacy-b','B')")
    with engine.connect() as connection:
        transaction = connection.begin()
        with pytest.raises(RuntimeError, match="no reconstructable exact-evidence"):
            v0023_m4_scoring.apply(connection)
        transaction.rollback()
        assert connection.exec_driver_sql("SELECT id,gate FROM phase_gate").all() == [
            ("legacy-b", "B")
        ]
    engine.dispose()


def test_source_snapshot_contains_no_profile_text_and_keywords_are_canonical(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(tmp_path / "snapshot.db"), queue_executor=PoisonExecutor()
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        session_id, _, _ = _seed_current_score(app, client)
        positive = "UNIQUEPOSMARKER"
        negative = "ULTRARAREPENALTYMARKER"
        response = client.put(
            "/api/briefs/current",
            json=_brief(
                session_id,
                positive_keywords=[positive],
                negative_keywords=[negative],
            ),
        )
        assert response.status_code == 200, response.text
        with app.state.database.sessions() as session:
            score = session.scalar(
                select(CandidateScore).where(
                    CandidateScore.is_current.is_(True),
                )
            )
            assert score is not None
            serialized = json.dumps(score.source_snapshot, sort_keys=True)
        assert '"text"' not in serialized
        assert '"snippet"' not in serialized
        assert "python" not in serialized.casefold()
        assert "aws professional" not in serialized.casefold()
        assert positive.casefold() not in serialized.casefold()
        assert negative.casefold() not in serialized.casefold()
        penalty_inputs = score.source_snapshot["penalty_inputs"]
        assert penalty_inputs["version"] == "sha256-normalized-v1"
        assert penalty_inputs["positive"]["count"] == 1
        assert penalty_inputs["negative"]["count"] == 1
        assert penalty_inputs["positive"]["sha256"] == [
            hashlib.sha256(positive.casefold().encode()).hexdigest()
        ]
        assert penalty_inputs["negative"]["sha256"] == [
            hashlib.sha256(negative.casefold().encode()).hexdigest()
        ]


def test_one_current_brief_and_config_are_enforced(tmp_path: Path) -> None:
    app = create_app(
        _settings(tmp_path / "current.db"), queue_executor=PoisonExecutor()
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        session_id, _, _ = _seed_current_score(app, client)
        with app.state.database.sessions() as session:
            assert (
                session.scalar(
                    select(func.count(RoleBrief.id)).where(
                        RoleBrief.session_id == session_id,
                        RoleBrief.superseded_at.is_(None),
                    )
                )
                == 1
            )
            assert (
                session.scalar(
                    select(func.count(ScoringConfig.id)).where(
                        ScoringConfig.session_id == session_id,
                        ScoringConfig.superseded_at.is_(None),
                    )
                )
                == 1
            )
            assert session.get(DashboardSession, session_id) is not None


def test_pure_kernel_and_local_persistence_import_boundaries() -> None:
    services = Path(__file__).parents[2] / "backend/linkedin_dashboard/services"
    for path in (services / "scoring").rglob("*.py"):
        source = path.read_text()
        imports: set[str] = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add(node.module or "")
        assert not any(name.startswith("sqlalchemy") for name in imports)
        assert not any("linkedin_dashboard.mcp" in name for name in imports)
    for name in ("scoring_persist.py", "scoring_service.py"):
        source = (services / name).read_text()
        assert "linkedin_dashboard.mcp" not in source
        assert "send_message" not in source


def test_unparseable_round_trip_requires_consumed_exact_section(tmp_path: Path) -> None:
    executor = SequenceExecutor(
        [
            {
                "url": "https://www.linkedin.com/search/results/people/",
                "sections": {"search_results": "Ada"},
                "references": {
                    "search_results": [
                        {"kind": "person", "url": "/in/ada/", "text": "Ada"}
                    ]
                },
            },
            {
                "url": "https://www.linkedin.com/in/ada/",
                "sections": {
                    "experience": "Experience\nPlatform Engineer\nAcme\nUnknown dates"
                },
            },
        ]
    )
    app = create_app(_settings(tmp_path / "unparseable.db"), queue_executor=executor)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        session_id = client.post("/api/session", json={"label": "parse"}).json()["id"]
        brief = client.post(
            "/api/briefs",
            json=_brief(
                session_id,
                required_experience_months=60,
                target_titles=[{"term": "Platform Engineer", "aliases": []}],
            ),
        ).json()
        search = client.post(
            "/api/searches",
            json={
                "session_id": session_id,
                "brief_id": brief["id"],
                "keywords": "platform",
            },
        ).json()
        assert _wait(app, search["job_id"]).state == "done"
        candidate_id = client.get(
            "/api/candidate-pool", params={"session_id": session_id}
        ).json()[0]["id"]
        client.post("/api/session/gates/A", json={"note": "reviewed"})
        enrichment = client.post(
            f"/api/candidates/{candidate_id}/enrich",
            json={"sections": ["experience"]},
        ).json()
        assert _wait(app, enrichment["job_id"]).state == "done"
        detail = client.get(f"/api/candidates/{candidate_id}").json()
        experience = next(
            signal for signal in detail["signals"] if signal["signal_id"] == "S-3"
        )
        assert experience["claims"][0]["missing_sections"] == [
            {"section_name": "experience", "reason": "unparseable"}
        ]

        with app.state.database.sessions() as session:
            current = session.scalar(
                select(CandidateScore).where(
                    CandidateScore.candidate_id == candidate_id,
                    CandidateScore.is_current.is_(True),
                )
            )
            section = session.scalar(
                select(ProfileSection).where(
                    ProfileSection.candidate_id == candidate_id,
                    ProfileSection.section_name == "experience",
                )
            )
            assert current is not None and section is not None
            staged = CandidateScore(
                id="unparseable-staged",
                candidate_id=candidate_id,
                brief_id=current.brief_id,
                weights_version=current.weights_version,
                scoring_config_id=current.scoring_config_id,
                stage=current.stage,
                score=None,
                score_lower=None,
                score_upper=None,
                confidence=0,
                confidence_band=None,
                calculation_status="unknown",
                active_signal_count=1,
                all_inert_attested=False,
                input_fingerprint="a" * 64,
                source_snapshot={},
                computed_at="2026-09-04T00:00:00+00:00",
                superseded_at=None,
                is_current=False,
            )
            signal = ScoreSignal(
                id="unparseable-signal",
                score_id=staged.id,
                signal_id="S-3",
                weight=1,
                verdict="unknown",
                rollup="unknown",
                raw_subscore=0,
                contribution=0,
                availability=0,
                note=None,
            )
            missing = MissingSetRecord(
                id="unparseable-set",
                candidate_id=candidate_id,
                score_signal_id=signal.id,
            )
            source = ScoreInputSection(
                score_id=staged.id,
                profile_section_id=section.id,
                content_sha256=section.content_sha256,
            )
        with app.state.database.sessions.begin() as session:
            session.add(staged)
            session.flush()
            session.add(signal)
            session.flush()
            session.add_all([missing, source])
        with pytest.raises(IntegrityError, match="invalid lineage"):
            with app.state.database.sessions.begin() as session:
                session.add(
                    SignalMissingSection(
                        id="wrong-unparseable",
                        missing_set_id=missing.id,
                        section_name="skills",
                        reason="unparseable",
                        section_error_id=None,
                    )
                )
        with app.state.database.sessions.begin() as session:
            session.add(
                SignalMissingSection(
                    id="valid-unparseable",
                    missing_set_id=missing.id,
                    section_name="experience",
                    reason="unparseable",
                    section_error_id=None,
                )
            )


@pytest.mark.parametrize("error_type", ("parse_error", "unparseable"))
def test_error_only_parse_failure_is_rooted_fetch_error(
    tmp_path: Path, error_type: str
) -> None:
    error = {
        "error_type": error_type,
        "error_message": "parser could not consume the returned section",
    }
    executor = SequenceExecutor(
        [
            {
                "url": "https://www.linkedin.com/search/results/people/",
                "sections": {"search_results": "Ada"},
                "section_errors": {
                    "search_results": {
                        "error_type": "partial",
                        "error_message": "one search segment was unavailable",
                    }
                },
                "references": {
                    "search_results": [
                        {"kind": "person", "url": "/in/ada/", "text": "Ada"}
                    ]
                },
            },
            {
                "url": "https://www.linkedin.com/in/ada/",
                "sections": {"main_profile": "Ada\nPlatform Engineer"},
                "section_errors": {"experience": error},
            },
        ]
    )
    app = create_app(
        _settings(tmp_path / f"error-{error_type}.db"), queue_executor=executor
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        session_id = client.post("/api/session", json={"label": "parse error"}).json()[
            "id"
        ]
        brief = client.post(
            "/api/briefs",
            json=_brief(session_id, required_experience_months=60),
        ).json()
        search = client.post(
            "/api/searches",
            json={
                "session_id": session_id,
                "brief_id": brief["id"],
                "keywords": "platform",
            },
        ).json()
        assert _wait(app, search["job_id"]).state == "done"
        candidate_id = client.get(
            "/api/candidate-pool", params={"session_id": session_id}
        ).json()[0]["id"]
        gate = client.post("/api/session/gates/A", json={"note": "reviewed"})
        assert gate.status_code == 201
        enrichment = client.post(
            f"/api/candidates/{candidate_id}/enrich",
            json={"sections": ["experience"]},
        ).json()
        assert _wait(app, enrichment["job_id"]).state == "done"
        detail = client.get(f"/api/candidates/{candidate_id}").json()
        experience = next(
            signal for signal in detail["signals"] if signal["signal_id"] == "S-3"
        )
        assert experience["claims"][0]["missing_sections"] == [
            {"section_name": "experience", "reason": "fetch_error"}
        ]
        with app.state.database.sessions() as session:
            errors = list(session.scalars(select(SectionError)))
            error_ids = {
                "search": next(
                    row.id for row in errors if row.search_run_id is not None
                ),
                "fetch": next(row.id for row in errors if row.fetch_id is not None),
            }
        for recursive in ("ON", "OFF"):
            with sqlite3.connect(app.state.database.path) as connection:
                register_sqlite_unicode_casefold(connection)
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute(f"PRAGMA recursive_triggers={recursive}")
                for root, error_id in error_ids.items():
                    with pytest.raises(
                        sqlite3.IntegrityError,
                        match=r"section error history is immutable|provenance",
                    ):
                        if root == "search":
                            connection.execute(
                                "UPDATE section_error SET candidate_id=? WHERE id=?",
                                (candidate_id, error_id),
                            )
                        else:
                            connection.execute(
                                "UPDATE section_error SET candidate_id=NULL,"
                                "fetch_id=NULL WHERE id=?",
                                (error_id,),
                            )
                    with pytest.raises(
                        sqlite3.IntegrityError,
                        match="section error already exists",
                    ):
                        connection.execute(
                            "INSERT INTO section_error "
                            "SELECT * FROM section_error WHERE id=? "
                            "ON CONFLICT(id) DO UPDATE SET error_message='forged'",
                            (error_id,),
                        )
                    with pytest.raises(
                        sqlite3.IntegrityError,
                        match="section error already exists",
                    ):
                        connection.execute(
                            "INSERT OR REPLACE INTO section_error "
                            "SELECT * FROM section_error WHERE id=?",
                            (error_id,),
                        )
                    with pytest.raises(
                        sqlite3.IntegrityError,
                        match="section error history is append-only",
                    ):
                        connection.execute(
                            "DELETE FROM section_error WHERE id=?", (error_id,)
                        )
                    assert connection.execute(
                        "SELECT count(*) FROM section_error WHERE id=?", (error_id,)
                    ).fetchone() == (1,)
        with sqlite3.connect(app.state.database.path) as connection:
            register_sqlite_unicode_casefold(connection)
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("DELETE FROM session WHERE id=?", (session_id,))
            assert connection.execute(
                "SELECT count(*) FROM section_error"
            ).fetchone() == (0,)
