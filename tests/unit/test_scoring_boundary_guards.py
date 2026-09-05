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
from linkedin_dashboard.db.migrations import (
    v0023_m4_scoring,
    v0024_m4_integrity_upgrade,
    v0025_m4_semantic_integrity,
    v0026_m4_manifest_convergence,
    v0027_m4_bounded_manifests,
    v0028_m4_text_storage,
    v0029_m4_claim_stage_identity,
)
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
from linkedin_dashboard.db.session import Database
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
    return {"url": "https://www.linkedin.com/in/ada/", "sections": sections}


def _downgrade_v25_schema_to_v24(connection: sqlite3.Connection) -> None:
    for name in v0029_m4_claim_stage_identity.TRIGGER_NAMES:
        connection.execute(f'DROP TRIGGER "{name}"')
    for name in v0028_m4_text_storage.TRIGGER_NAMES:
        connection.execute(f'DROP TRIGGER "{name}"')
    for name in v0027_m4_bounded_manifests.TRIGGER_NAMES:
        connection.execute(f'DROP TRIGGER "{name}"')
    for name in (
        "score_claim_finalize_v25",
        "score_finalize_signal_set_v25",
        "signal_coverage_shape_v25",
        "phase_gate_manifest_insert",
        "role_brief_append_only",
        "role_brief_scoring_insert_v25",
        "role_brief_scoring_seal_v25",
    ):
        connection.execute(f'DROP TRIGGER "{name}"')
    connection.execute("DROP INDEX score_signal_identity_v25")
    connection.execute("ALTER TABLE role_brief DROP COLUMN scoring_inputs")
    connection.execute(
        next(
            statement
            for statement in v0023_m4_scoring.STATEMENTS
            if statement.startswith("CREATE TRIGGER role_brief_append_only")
        )
    )
    connection.execute(
        next(
            statement
            for statement in v0024_m4_integrity_upgrade.STATEMENTS
            if statement.startswith("CREATE TRIGGER phase_gate_manifest_insert")
        )
    )
    connection.execute(
        "DELETE FROM schema_migration WHERE version IN (?,?,?,?,?)",
        (
            v0025_m4_semantic_integrity.VERSION,
            v0026_m4_manifest_convergence.VERSION,
            v0027_m4_bounded_manifests.VERSION,
            v0028_m4_text_storage.VERSION,
            v0029_m4_claim_stage_identity.VERSION,
        ),
    )


def _seed_profile_database(
    path: Path, *, stage_one: dict[str, Any], stage_two: dict[str, Any]
) -> str:
    executor = SequenceExecutor([_search_result(), stage_one, stage_two])
    app = create_app(_settings(path), queue_executor=executor)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        session_id = client.post("/api/session", json={"label": "migration"}).json()[
            "id"
        ]
        brief = client.post(
            "/api/briefs", json=_brief(session_id, required_credentials=[])
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
        first = client.post(f"/api/candidates/{candidate_id}/enrich", json={}).json()
        assert _wait(app, first["job_id"]).state == "done"
        second = client.post(
            f"/api/candidates/{candidate_id}/enrich",
            json={"sections": ["skills"]},
        ).json()
        assert _wait(app, second["job_id"]).state == "done"
        with app.state.database.sessions() as session:
            score_id = session.scalar(
                select(CandidateScore.id).where(
                    CandidateScore.candidate_id == candidate_id,
                    CandidateScore.is_current.is_(True),
                )
            )
            assert score_id is not None
            return score_id


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
            with pytest.raises(
                sqlite3.IntegrityError,
                match=r"version already exists|scoring inputs are not canonical",
            ):
                connection.execute(
                    "INSERT OR REPLACE INTO role_brief "
                    "(id,session_id,version,created_at,sealed_at,superseded_at,"
                    "job_description,target_titles,location,industries,"
                    "positive_keywords,negative_keywords,message_tone,"
                    "required_experience_months,weights_version,scoring_inputs) "
                    "SELECT 'replacement-brief',session_id,version+1,'now',"
                    "'now',NULL,job_description,target_titles,location,industries,"
                    "positive_keywords,negative_keywords,message_tone,"
                    "required_experience_months,weights_version,scoring_inputs "
                    "FROM role_brief "
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


def test_ranked_headline_never_resurrects_an_older_parse(tmp_path: Path) -> None:
    executor = SequenceExecutor(
        [
            _search_result(),
            _profile_result(main_profile="Ada Example\nPlatform Engineer"),
            _profile_result(main_profile="Ada Example"),
        ]
    )
    app = create_app(
        _settings(tmp_path / "headline-lineage.db"), queue_executor=executor
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        session_id = client.post("/api/session", json={"label": "headline"}).json()[
            "id"
        ]
        brief = client.post("/api/briefs", json=_brief(session_id)).json()
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
        assert (
            client.post("/api/session/gates/A", json={"note": "reviewed"}).status_code
            == 201
        )

        first = client.post(
            f"/api/candidates/{candidate_id}/enrich",
            json={},
        ).json()
        assert _wait(app, first["job_id"]).state == "done"
        ranked = client.get("/api/candidates", params={"session_id": session_id})
        assert ranked.status_code == 200
        assert ranked.json()[0]["headline"] == "Platform Engineer"

        second = client.post(
            f"/api/candidates/{candidate_id}/enrich",
            json={"sections": ["education"]},
        ).json()
        assert _wait(app, second["job_id"]).state == "done"
        ranked = client.get("/api/candidates", params={"session_id": session_id})
        assert ranked.status_code == 200
        assert ranked.json()[0]["headline"] is None
        detail = client.get(f"/api/candidates/{candidate_id}").json()
        assert not any(field["field_key"] == "headline" for field in detail["fields"])


def test_gate_b_rejects_selected_evidence_with_wrong_polarity(tmp_path: Path) -> None:
    skills = " ".join(f"skill{index}" for index in range(10))
    executor = SequenceExecutor(
        [
            _search_result(),
            _profile_result(main_profile="Ada Example"),
            _profile_result(skills=skills),
        ]
    )
    app = create_app(_settings(tmp_path / "gate-polarity.db"), queue_executor=executor)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        session_id = client.post("/api/session", json={"label": "polarity"}).json()[
            "id"
        ]
        brief = client.post(
            "/api/briefs",
            json=_brief(
                session_id,
                required_skills=[
                    {"term": f"skill{index}", "aliases": []} for index in range(10)
                ],
                required_credentials=[],
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
        assert (
            client.post("/api/session/gates/A", json={"note": "reviewed"}).status_code
            == 201
        )
        stage_one = client.post(
            f"/api/candidates/{candidate_id}/enrich",
            json={},
        ).json()
        assert _wait(app, stage_one["job_id"]).state == "done"
        enrich = client.post(
            f"/api/candidates/{candidate_id}/enrich",
            json={"sections": ["skills"]},
        ).json()
        assert _wait(app, enrich["job_id"]).state == "done"
        detail = client.get(f"/api/candidates/{candidate_id}").json()
        evidence_ids = [
            item["id"]
            for signal in detail["signals"]
            for claim in signal["claims"]
            for item in claim["evidence"]
            if item["availability"]["state"] == "available"
        ]
        assert len(evidence_ids) == 10
        score_id = detail["score"]["score_id"]
        fingerprint = detail["score"]["input_fingerprint"]
        with sqlite3.connect(app.state.database.path) as connection:
            trigger_sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' "
                "AND name='evidence_m4_is_immutable'"
            ).fetchone()[0]
            connection.execute("DROP TRIGGER evidence_m4_is_immutable")
            connection.execute(
                "UPDATE evidence SET polarity='contradicting' WHERE id=?",
                (evidence_ids[0],),
            )
            connection.execute(trigger_sql)

        response = client.post(
            "/api/session/gates/B", json={"evidence_ids": evidence_ids}
        )
        assert response.status_code == 409
        assert response.json()["detail"] == (
            "Gate B evidence is stale, purged, or cross-session"
        )
        manifest = json.dumps(
            [
                {
                    "evidence_id": evidence_id,
                    "score_id": score_id,
                    "input_fingerprint": fingerprint,
                }
                for evidence_id in evidence_ids
            ],
            separators=(",", ":"),
        )
        with sqlite3.connect(app.state.database.path) as connection:
            with pytest.raises(
                sqlite3.IntegrityError,
                match="ten current exact evidence spans",
            ):
                connection.execute(
                    "INSERT INTO phase_gate "
                    "(id,session_id,gate,accepted_at,accepted_note,evidence_manifest) "
                    "VALUES ('raw-gate-b',?,'B','now','reviewed',?)",
                    (session_id, manifest),
                )


@pytest.mark.parametrize("recursive", ("ON", "OFF"))
def test_raw_claims_require_polarity_and_canonical_coverage(
    tmp_path: Path, recursive: str
) -> None:
    executor = SequenceExecutor(
        [
            _search_result(),
            _profile_result(main_profile="Ada Example", experience="Rust"),
            _profile_result(skills="Rust"),
        ]
    )
    app = create_app(
        _settings(tmp_path / f"claim-semantics-{recursive}.db"),
        queue_executor=executor,
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        session_id = client.post("/api/session", json={"label": "claims"}).json()["id"]
        brief = client.post(
            "/api/briefs",
            json=_brief(session_id, required_credentials=[]),
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
        assert (
            client.post("/api/session/gates/A", json={"note": "reviewed"}).status_code
            == 201
        )
        stage_one = client.post(
            f"/api/candidates/{candidate_id}/enrich", json={}
        ).json()
        assert _wait(app, stage_one["job_id"]).state == "done"
        stage_two = client.post(
            f"/api/candidates/{candidate_id}/enrich",
            json={"sections": ["skills"]},
        ).json()
        assert _wait(app, stage_two["job_id"]).state == "done"
        detail = client.get(f"/api/candidates/{candidate_id}").json()
        score_id = detail["score"]["score_id"]

        with sqlite3.connect(app.state.database.path) as connection:
            register_sqlite_unicode_casefold(connection)
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(f"PRAGMA recursive_triggers={recursive}")
            immutable_sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' "
                "AND name='score_content_is_immutable'"
            ).fetchone()[0]
            connection.execute("DROP TRIGGER score_content_is_immutable")
            connection.execute("UPDATE score SET is_current=0 WHERE id=?", (score_id,))
            connection.execute(immutable_sql)
            signal_id = connection.execute(
                "SELECT id FROM score_signal WHERE score_id=? AND signal_id='S-1'",
                (score_id,),
            ).fetchone()[0]
            claim_trigger = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' "
                "AND name='score_claim_is_immutable'"
            ).fetchone()[0]
            connection.execute("DROP TRIGGER score_claim_is_immutable")
            connection.execute(
                "UPDATE score_claim SET claim_key='legacy:S-1:python' "
                "WHERE score_signal_id=? AND claim_key='S-1:python'",
                (signal_id,),
            )
            connection.execute(claim_trigger)
            section_id, content_hash = connection.execute(
                "SELECT score_input_section.profile_section_id,"
                "score_input_section.content_sha256 FROM score_input_section "
                "JOIN profile_section ON profile_section.id=profile_section_id "
                "WHERE score_id=? AND section_name='skills'",
                (score_id,),
            ).fetchone()

            connection.execute(
                "INSERT INTO coverage_set VALUES "
                "('forged-coverage',?,?, '[\"skills\"]')",
                (candidate_id, signal_id),
            )
            for row_id, terms, aliases, matcher in (
                ("empty-terms", "[]", "[]", "scoring-v1"),
                ("unknown-matcher", '["python"]', "[]", "unknown-v9"),
                ("duplicate-terms", '["python","python"]', "[]", "scoring-v1"),
                ("unsorted-terms", '["rust","python"]', "[]", "scoring-v1"),
                (
                    "noncanonical-term",
                    '["\\uff30ython"]',
                    "[]",
                    "scoring-v1",
                ),
                (
                    "duplicate-aliases",
                    '["python"]',
                    '["py","py"]',
                    "scoring-v1",
                ),
                (
                    "unsorted-aliases",
                    '["python"]',
                    '["python3","py"]',
                    "scoring-v1",
                ),
                (
                    "overlapping-alias",
                    '["python"]',
                    '["python"]',
                    "scoring-v1",
                ),
            ):
                with pytest.raises(
                    sqlite3.IntegrityError,
                    match="absence coverage is not canonical",
                ):
                    connection.execute(
                        "INSERT INTO signal_coverage VALUES (?,?,?,?,?,?,?)",
                        (
                            row_id,
                            "forged-coverage",
                            section_id,
                            content_hash,
                            terms,
                            aliases,
                            matcher,
                        ),
                    )
            connection.execute(
                "INSERT INTO signal_coverage VALUES "
                "('wrong-terms','forged-coverage',?,?, '[\"rust\"]','[]',"
                "'scoring-v1')",
                (section_id, content_hash),
            )
            with pytest.raises(
                sqlite3.IntegrityError,
                match="score claim semantics do not match brief",
            ):
                connection.execute(
                    "INSERT INTO score_claim VALUES "
                    "('wrong-coverage',?,'S-1:python','Python','not_matched',NULL,"
                    "'forged-coverage',NULL)",
                    (signal_id,),
                )
            connection.execute(
                "INSERT INTO candidate "
                "(id,session_id,username,profile_url,first_seen_at,stage,"
                "retrieval_status) SELECT 'inactive-s3-candidate',session_id,"
                "'inactive-s3','https://www.linkedin.com/in/inactive-s3/',"
                "'now','discovered','pending' FROM candidate WHERE id=?",
                (candidate_id,),
            )
            connection.execute(
                "INSERT INTO score "
                "SELECT 'inactive-s3-score','inactive-s3-candidate',brief_id,"
                "weights_version,"
                "scoring_config_id,stage,NULL,NULL,NULL,0,NULL,'unknown',1,0,"
                "'c' || substr(input_fingerprint,2),"
                '\'{"active_signal_ids":["S-3"]}\',computed_at,NULL,0 '
                "FROM score WHERE id=?",
                (score_id,),
            )
            connection.execute(
                "INSERT INTO score_signal "
                "(id,score_id,signal_id,weight,verdict,rollup,raw_subscore,"
                "contribution,availability,note) SELECT "
                "'inactive-s3-signal','inactive-s3-score','S-3',"
                "json_extract(cfg.weights,'$.\"S-3\"'),'unknown','unknown',0,0,0,NULL "
                "FROM scoring_config cfg JOIN score s "
                "ON s.scoring_config_id=cfg.id WHERE s.id='inactive-s3-score'"
            )
            connection.execute(
                "INSERT INTO missing_set VALUES "
                "('inactive-s3-missing','inactive-s3-candidate',"
                "'inactive-s3-signal')"
            )
            with pytest.raises(
                sqlite3.IntegrityError,
                match="score claim semantics do not match brief",
            ):
                connection.execute(
                    "INSERT INTO score_claim VALUES "
                    "('inactive-s3-claim','inactive-s3-signal',"
                    "'S-3:experience-depth','experience','unknown',NULL,NULL,"
                    "'inactive-s3-missing')"
                )
            connection.execute(
                "INSERT INTO evidence_set VALUES ('mixed-set',?,?)",
                (candidate_id, signal_id),
            )
            for evidence_id, polarity in (
                ("mixed-supporting", "supporting"),
                ("mixed-contradicting", "contradicting"),
            ):
                connection.execute(
                    "INSERT INTO evidence "
                    "(id,score_signal_id,evidence_set_id,section_name,"
                    "profile_section_id,content_sha256,span_start,span_end,snippet,"
                    "matcher,matched_term,polarity) VALUES "
                    "(?,?,?,'skills',?,?,0,4,'Rust','exact','Rust',?)",
                    (
                        evidence_id,
                        signal_id,
                        "mixed-set",
                        section_id,
                        content_hash,
                        polarity,
                    ),
                )
            with pytest.raises(
                sqlite3.IntegrityError,
                match="score claim semantics do not match brief",
            ):
                connection.execute(
                    "INSERT INTO score_claim VALUES "
                    "('mixed-claim',?,'S-1:forged','Forged','matched',"
                    "'mixed-set',NULL,NULL)",
                    (signal_id,),
                )


@pytest.mark.parametrize("recursive", ("ON", "OFF"))
@pytest.mark.parametrize("defect", ("duplicate", "wrong_weight", "wrong_set"))
def test_raw_score_signals_require_unique_exact_configured_set(
    tmp_path: Path, recursive: str, defect: str
) -> None:
    app = create_app(
        _settings(tmp_path / f"signal-set-{defect}-{recursive}.db"),
        queue_executor=PoisonExecutor(),
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        _, _, score_id = _seed_current_score(app, client)
        with sqlite3.connect(app.state.database.path) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(f"PRAGMA recursive_triggers={recursive}")
            connection.execute("BEGIN")
            score_trigger = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' "
                "AND name='score_content_is_immutable'"
            ).fetchone()[0]
            connection.execute("DROP TRIGGER score_content_is_immutable")
            connection.execute("UPDATE score SET is_current=0 WHERE id=?", (score_id,))
            connection.execute(score_trigger)
            first = connection.execute(
                "SELECT id,signal_id,weight,verdict,rollup,raw_subscore,"
                "contribution,availability,note FROM score_signal "
                "WHERE score_id=? ORDER BY signal_id LIMIT 1",
                (score_id,),
            ).fetchone()
            assert first is not None
            if defect == "duplicate":
                with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint"):
                    connection.execute(
                        "INSERT INTO score_signal VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (
                            "duplicate-signal",
                            score_id,
                            *first[1:],
                        ),
                    )
            else:
                child_trigger = connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type='trigger' "
                    "AND name='score_child_is_immutable'"
                ).fetchone()[0]
                connection.execute("DROP TRIGGER score_child_is_immutable")
                if defect == "wrong_weight":
                    connection.execute(
                        "UPDATE score_signal SET weight=weight+1 WHERE id=?",
                        (first[0],),
                    )
                else:
                    rows = connection.execute(
                        "SELECT id,signal_id FROM score_signal WHERE score_id=? "
                        "ORDER BY signal_id",
                        (score_id,),
                    ).fetchall()
                    assert len(rows) == 2
                    replacement_weight = connection.execute(
                        "SELECT json_extract(weights, '$.\"' || ? || '\"') "
                        "FROM scoring_config JOIN score "
                        "ON score.scoring_config_id=scoring_config.id "
                        "WHERE score.id=?",
                        (rows[0][1], score_id),
                    ).fetchone()[0]
                    connection.execute("DROP INDEX score_signal_identity_v25")
                    connection.execute(
                        "UPDATE score_signal SET signal_id=?,weight=? WHERE id=?",
                        (rows[0][1], replacement_weight, rows[1][0]),
                    )
                connection.execute(child_trigger)
                with pytest.raises(
                    sqlite3.IntegrityError,
                    match=(
                        r"score (signal set does not match snapshot|"
                        r"claim set does not match brief)"
                    ),
                ):
                    connection.execute(
                        "UPDATE score SET is_current=1 WHERE id=?", (score_id,)
                    )
            connection.rollback()


def test_v25_upgrade_rejects_existing_mixed_polarity_claim(tmp_path: Path) -> None:
    path = tmp_path / "mixed-polarity-upgrade.db"
    executor = SequenceExecutor(
        [
            _search_result(),
            _profile_result(main_profile="Ada Python", experience="Python"),
            _profile_result(skills="Python"),
        ]
    )
    app = create_app(_settings(path), queue_executor=executor)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        session_id = client.post("/api/session", json={"label": "upgrade"}).json()["id"]
        brief = client.post(
            "/api/briefs", json=_brief(session_id, required_credentials=[])
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
        stage_one = client.post(
            f"/api/candidates/{candidate_id}/enrich", json={}
        ).json()
        assert _wait(app, stage_one["job_id"]).state == "done"
        stage_two = client.post(
            f"/api/candidates/{candidate_id}/enrich",
            json={"sections": ["skills"]},
        ).json()
        assert _wait(app, stage_two["job_id"]).state == "done"

    with sqlite3.connect(path) as connection:
        register_sqlite_unicode_casefold(connection)
        evidence_rows = connection.execute(
            "SELECT e.id FROM evidence e JOIN score_claim claim "
            "ON claim.evidence_set_id=e.evidence_set_id "
            "WHERE claim.verdict='matched' ORDER BY e.id"
        ).fetchall()
        assert len(evidence_rows) >= 2
        _downgrade_v25_schema_to_v24(connection)
        evidence_trigger = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' "
            "AND name='evidence_m4_is_immutable'"
        ).fetchone()[0]
        connection.execute("DROP TRIGGER evidence_m4_is_immutable")
        connection.execute(
            "UPDATE evidence SET polarity='contradicting' WHERE id=?",
            (evidence_rows[0][0],),
        )
        connection.execute(evidence_trigger)
        baseline = connection.execute(
            "SELECT id,polarity FROM evidence ORDER BY id"
        ).fetchall()

    upgraded = Database(path)
    with pytest.raises(RuntimeError, match="incompatible polarity"):
        upgraded.initialize()
    upgraded.dispose()
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute(
                "SELECT id,polarity FROM evidence ORDER BY id"
            ).fetchall()
            == baseline
        )
        assert (
            connection.execute(
                "SELECT 1 FROM schema_migration WHERE version=?",
                (v0025_m4_semantic_integrity.VERSION,),
            ).fetchone()
            is None
        )
        assert "scoring_inputs" not in {
            row[1] for row in connection.execute("PRAGMA table_info(role_brief)")
        }


@pytest.mark.parametrize("defect", ("coverage", "weight"))
def test_v25_upgrade_rejects_existing_semantic_corruption(
    tmp_path: Path, defect: str
) -> None:
    path = tmp_path / f"semantic-{defect}-upgrade.db"
    score_id = _seed_profile_database(
        path,
        stage_one=_profile_result(main_profile="Ada", experience="Rust"),
        stage_two=_profile_result(skills="Rust"),
    )
    with sqlite3.connect(path) as connection:
        register_sqlite_unicode_casefold(connection)
        _downgrade_v25_schema_to_v24(connection)
        if defect == "coverage":
            immutable_sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' "
                "AND name='signal_coverage_no_update'"
            ).fetchone()[0]
            connection.execute("DROP TRIGGER signal_coverage_no_update")
            connection.execute(
                "UPDATE signal_coverage SET normalized_terms='[\"rust\"]' "
                "WHERE id=(SELECT id FROM signal_coverage LIMIT 1)"
            )
            connection.execute(immutable_sql)
            expected = "noncanonical absence coverage"
        else:
            immutable_sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' "
                "AND name='score_child_is_immutable'"
            ).fetchone()[0]
            connection.execute("DROP TRIGGER score_child_is_immutable")
            connection.execute(
                "UPDATE score_signal SET weight=weight+1 "
                "WHERE id=(SELECT id FROM score_signal WHERE score_id=? LIMIT 1)",
                (score_id,),
            )
            connection.execute(immutable_sql)
            expected = "invalid signal set or weight"

    upgraded = Database(path)
    with pytest.raises(RuntimeError, match=expected):
        upgraded.initialize()
    upgraded.dispose()
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM schema_migration WHERE version=?",
                (v0025_m4_semantic_integrity.VERSION,),
            ).fetchone()
            is None
        )


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


def test_s3_cross_category_coverage_is_globally_canonical(tmp_path: Path) -> None:
    executor = SequenceExecutor(
        [
            _search_result(),
            _profile_result(
                experience="Experience\nCOBOL Operator\nJan 2018 - Dec 2023"
            ),
        ]
    )
    app = create_app(_settings(tmp_path / "s3-canonical.db"), queue_executor=executor)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        session_id = client.post("/api/session", json={"label": "s3"}).json()["id"]
        brief = client.post(
            "/api/briefs",
            json=_brief(
                session_id,
                required_skills=[
                    {"term": "Python", "aliases": ["Shared"]},
                    {"term": "Go", "aliases": ["Golang"]},
                ],
                target_titles=[{"term": "Zelda", "aliases": ["shared", "Go"]}],
                required_credentials=[],
                required_experience_months=60,
            ),
        ).json()
        assert brief["id"]
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
        assert (
            client.post("/api/session/gates/A", json={"note": "reviewed"}).status_code
            == 201
        )
        enrichment = client.post(
            f"/api/candidates/{candidate_id}/enrich",
            json={"sections": ["experience"]},
        ).json()
        assert _wait(app, enrichment["job_id"]).state == "done"

        with app.state.database.engine.connect() as connection:
            coverage = connection.exec_driver_sql(
                "SELECT coverage.normalized_terms,coverage.aliases,"
                "coverage.matcher_version FROM signal_coverage coverage "
                "JOIN coverage_set cs ON cs.id=coverage.coverage_set_id "
                "JOIN score_signal ss ON ss.id=cs.score_signal_id "
                "JOIN score s ON s.id=ss.score_id "
                "WHERE s.candidate_id=? AND s.is_current=1 "
                "AND ss.signal_id='S-3'",
                (candidate_id,),
            ).one()
        assert json.loads(coverage.normalized_terms) == ["go", "python", "zelda"]
        assert json.loads(coverage.aliases) == ["golang", "shared"]
        assert coverage.matcher_version == "scoring-v1"


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
