from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from linkedin_dashboard.db import session as db_session
from linkedin_dashboard.db.migrations import v0029_m4_claim_stage_identity as v29
from linkedin_dashboard.db.models import (
    Candidate,
    CandidateScore,
    RoleBrief,
    ScoringConfig,
)
from linkedin_dashboard.db.session import Database
from linkedin_dashboard.db.unicode_identity import register_sqlite_unicode_casefold
from linkedin_dashboard.main import create_app
from linkedin_dashboard.services import scoring_persist
from linkedin_dashboard.services.scoring.aggregate import calculate_score
from linkedin_dashboard.services.scoring.signals import active_signal_ids
from linkedin_dashboard.services.scoring.types import ScoreStage
from test_scoring_boundary_guards import (
    SequenceExecutor,
    _brief,
    _profile_result,
    _search_result,
    _settings,
    _wait,
)


@pytest.fixture
def scored(tmp_path: Path) -> Iterator[tuple[Any, TestClient, str, str]]:
    executor = SequenceExecutor(
        [
            _search_result(),
            _profile_result(
                main_profile="Ada Example\nPlatform Engineer",
                experience="Platform Engineer\nExample Corp\n2 yrs",
            ),
            _profile_result(skills="\n".join(["Python", "Rust"] * 10)),
        ]
    )
    app = create_app(_settings(tmp_path / "identity.db"), queue_executor=executor)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        session_id = client.post("/api/session", json={"label": "identity"}).json()[
            "id"
        ]
        brief = client.post(
            "/api/briefs",
            json=_brief(
                session_id,
                required_skills=[{"term": "Python"}, {"term": "Rust"}],
                required_credentials=[],
            ),
        ).json()
        job = client.post(
            "/api/searches",
            json={
                "session_id": session_id,
                "brief_id": brief["id"],
                "keywords": "platform",
            },
        ).json()
        assert _wait(app, job["job_id"]).state == "done"
        candidate_id = client.get(
            "/api/candidate-pool", params={"session_id": session_id}
        ).json()[0]["id"]
        assert (
            client.post("/api/session/gates/A", json={"note": "reviewed"}).status_code
            == 201
        )
        for payload in ({}, {"sections": ["skills"]}):
            job = client.post(
                f"/api/candidates/{candidate_id}/enrich", json=payload
            ).json()
            assert _wait(app, job["job_id"]).state == "done"
        yield app, client, session_id, candidate_id


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    register_sqlite_unicode_casefold(connection)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA trusted_schema=OFF")
    return connection


def _temporarily_unstage(connection: sqlite3.Connection, score_id: str) -> None:
    # Deliberately simulate a pre-finalization score while preserving every other
    # guard. This is fixture setup, not the guard being tested.
    sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE name='score_content_is_immutable'"
    ).fetchone()[0]
    connection.execute("DROP TRIGGER score_content_is_immutable")
    connection.execute("UPDATE score SET is_current=0 WHERE id=?", (score_id,))
    connection.execute(sql)


@pytest.mark.parametrize("recursive", ("ON", "OFF"))
@pytest.mark.parametrize("verdict", ("matched", "contradicted", "unknown"))
@pytest.mark.parametrize("defect", ("key", "label", "noncanonical_key", "blob_label"))
def test_every_claim_verdict_is_bound_to_brief(
    scored: tuple[Any, TestClient, str, str],
    recursive: str,
    verdict: str,
    defect: str,
) -> None:
    app, client, _, candidate_id = scored
    detail = client.get(f"/api/candidates/{candidate_id}").json()
    score_id = detail["score"]["score_id"]
    with _connect(app.state.database.path) as connection:
        connection.execute(f"PRAGMA recursive_triggers={recursive}")
        _temporarily_unstage(connection, score_id)
        signal_id = connection.execute(
            "SELECT id FROM score_signal WHERE score_id=? AND signal_id='S-1'",
            (score_id,),
        ).fetchone()[0]
        # Free the genuine claim's unique key/provenance with a controlled fixture
        # deletion; re-create that trigger before the adversarial INSERT.
        sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='score_claim_no_delete'"
        ).fetchone()[0]
        evidence_set = connection.execute(
            "SELECT evidence_set_id FROM score_claim WHERE score_signal_id=? "
            "AND claim_key='S-1:python'",
            (signal_id,),
        ).fetchone()[0]
        connection.execute("DROP TRIGGER score_claim_no_delete")
        connection.execute(
            "DELETE FROM score_claim WHERE score_signal_id=? "
            "AND claim_key='S-1:python'",
            (signal_id,),
        )
        connection.execute(sql)
        if verdict == "contradicted":
            sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE name='evidence_m4_is_immutable'"
            ).fetchone()[0]
            connection.execute("DROP TRIGGER evidence_m4_is_immutable")
            connection.execute(
                "UPDATE evidence SET polarity='contradicting' WHERE evidence_set_id=?",
                (evidence_set,),
            )
            connection.execute(sql)
        missing_set = None
        if verdict == "unknown":
            evidence_set = None
            missing_set = "forged-missing"
            connection.execute(
                "INSERT INTO missing_set VALUES (?,?,?)",
                (missing_set, candidate_id, signal_id),
            )
            connection.execute(
                "INSERT INTO signal_missing_section VALUES "
                "('forged-reason',?,'experience','unparseable',NULL)",
                (missing_set,),
            )
        key = "S-1:python"
        label: str | bytes = "Python"
        if defect == "key":
            key = "S-1:invented"
        elif defect == "label":
            label = "Unrequested qualification"
        elif defect == "noncanonical_key":
            key = "S-1:Python"
        else:
            label = b"Python"
        with pytest.raises(sqlite3.IntegrityError, match=r"claim.*brief"):
            connection.execute(
                "INSERT INTO score_claim VALUES ('forged',?,?,?,?,?,NULL,?)",
                (signal_id, key, label, verdict, evidence_set, missing_set),
            )


@pytest.mark.parametrize("kind", ("all_inert", "months_only", "credentials_only"))
def test_ranked_headline_is_display_data_without_scoring_sources(
    scored: tuple[Any, TestClient, str, str], kind: str
) -> None:
    app, client, session_id, _ = scored
    changes: dict[str, Any] = {
        "required_skills": [],
        "required_credentials": [],
        "positive_keywords": ["platform"],
    }
    if kind == "months_only":
        changes["required_experience_months"] = 12
    elif kind == "credentials_only":
        changes["required_credentials"] = [{"term": "AWS"}]
        assert (
            client.put(
                "/api/briefs/current",
                json=_brief(session_id, required_credentials=[{"term": "AWS"}]),
            ).status_code
            == 200
        )
        config = client.get("/api/weights").json()
        config["weights"]["S-8"] = 8
        assert (
            client.put(
                "/api/weights/current",
                json={
                    "expected_version": config["version"],
                    "weights": config["weights"],
                    "metro_region_equivalences": config["metro_region_equivalences"],
                },
            ).status_code
            == 200
        )
    response = client.put("/api/briefs/current", json=_brief(session_id, **changes))
    assert response.status_code == 200, response.json()
    ranked = client.get("/api/candidates", params={"session_id": session_id}).json()[0]
    assert ranked["headline"] == "Platform Engineer"
    with _connect(app.state.database.path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM score_input_section src JOIN profile_section ps "
            "ON ps.id=src.profile_section_id WHERE src.score_id=? "
            "AND ps.section_name='main_profile'",
            (ranked["score_id"],),
        ).fetchone() == (0,)


def test_rescore_stage_is_an_immutable_input(
    scored: tuple[Any, TestClient, str, str],
) -> None:
    app, client, session_id, candidate_id = scored
    # Recreate the pre-fix state: scoring inputs unchanged, candidate promoted
    # after a provisional score. Preserve the old row when correcting it.
    with _connect(app.state.database.path) as connection:
        connection.execute(
            "UPDATE candidate SET stage='stage1' WHERE id=?", (candidate_id,)
        )
    assert (
        client.put(
            "/api/briefs/current",
            json=_brief(session_id, required_credentials=[]),
        ).status_code
        == 200
    )
    provisional = client.post(f"/api/candidates/{candidate_id}/rescore").json()
    assert provisional["stage"] == "provisional"
    with _connect(app.state.database.path) as connection:
        old = connection.execute(
            "SELECT * FROM score WHERE id=?", (provisional["score_id"],)
        ).fetchone()
        connection.execute(
            "UPDATE candidate SET stage='stage2' WHERE id=?", (candidate_id,)
        )
    enriched = client.post(f"/api/candidates/{candidate_id}/rescore").json()
    assert enriched["stage"] == "enriched"
    assert enriched["score_id"] != provisional["score_id"]
    assert enriched["input_fingerprint"] != provisional["input_fingerprint"]
    assert enriched["score"] == provisional["score"]
    assert (
        client.post(f"/api/candidates/{candidate_id}/rescore").json()["score_id"]
        == enriched["score_id"]
    )
    with _connect(app.state.database.path) as connection:
        historical = connection.execute(
            "SELECT * FROM score WHERE id=?", (provisional["score_id"],)
        ).fetchone()
        assert historical[:-2] == old[:-2]
        assert historical[-2] is not None and historical[-1] == 0
        payload = json.loads(
            connection.execute(
                "SELECT source_snapshot FROM score WHERE id=?", (enriched["score_id"],)
            ).fetchone()[0]
        )
        assert payload["scoring_stage"] == "enriched"


@pytest.mark.parametrize("defect", ("missing", "key", "label", "stage"))
def test_gate_b_revalidates_whole_claim_set_and_stage(
    scored: tuple[Any, TestClient, str, str], defect: str
) -> None:
    app, client, session_id, candidate_id = scored
    detail = client.get(f"/api/candidates/{candidate_id}").json()
    score = detail["score"]
    evidence_ids = [
        evidence["id"]
        for signal in detail["signals"]
        for claim in signal["claims"]
        for evidence in claim["evidence"]
    ]
    assert len(evidence_ids) == 20
    evidence_ids = evidence_ids[:10]
    with _connect(app.state.database.path) as connection:
        if defect == "stage":
            connection.execute(
                "UPDATE candidate SET stage='stage1' WHERE id=?", (candidate_id,)
            )
        else:
            trigger = (
                "score_claim_no_delete"
                if defect == "missing"
                else "score_claim_is_immutable"
            )
            sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE name=?", (trigger,)
            ).fetchone()[0]
            connection.execute(f'DROP TRIGGER "{trigger}"')
            claim_index = -1 if defect == "missing" else 0
            claim_id = detail["signals"][0]["claims"][claim_index]["id"]
            if defect == "missing":
                connection.execute("DELETE FROM score_claim WHERE id=?", (claim_id,))
            elif defect == "key":
                connection.execute(
                    "UPDATE score_claim SET claim_key='S-1:invented' WHERE id=?",
                    (claim_id,),
                )
            else:
                connection.execute(
                    "UPDATE score_claim SET display_term='Forged qualification' "
                    "WHERE id=?",
                    (claim_id,),
                )
            connection.execute(sql)
    response = client.post("/api/session/gates/B", json={"evidence_ids": evidence_ids})
    assert response.status_code == 409
    manifest = json.dumps(
        [
            {
                "evidence_id": evidence_id,
                "score_id": score["score_id"],
                "input_fingerprint": score["input_fingerprint"],
            }
            for evidence_id in evidence_ids
        ]
    )
    with _connect(app.state.database.path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="Gate B"):
            connection.execute(
                "INSERT INTO phase_gate VALUES ('raw-gate',?,'B','now','reviewed',?)",
                (session_id, manifest),
            )
        assert connection.execute(
            "SELECT count(*) FROM phase_gate WHERE gate='B'"
        ).fetchone() == (0,)


@pytest.mark.parametrize("defect", ("missing", "label", "stage"))
def test_score_finalization_revalidates_claim_set_and_stage(
    scored: tuple[Any, TestClient, str, str], defect: str
) -> None:
    app, client, _, candidate_id = scored
    detail = client.get(f"/api/candidates/{candidate_id}").json()
    score_id = detail["score"]["score_id"]
    with _connect(app.state.database.path) as connection:
        _temporarily_unstage(connection, score_id)
        if defect == "stage":
            connection.execute(
                "UPDATE candidate SET stage='stage1' WHERE id=?", (candidate_id,)
            )
        else:
            trigger = (
                "score_claim_no_delete"
                if defect == "missing"
                else "score_claim_is_immutable"
            )
            sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE name=?", (trigger,)
            ).fetchone()[0]
            connection.execute(f'DROP TRIGGER "{trigger}"')
            claim_id = detail["signals"][0]["claims"][0]["id"]
            if defect == "missing":
                connection.execute("DELETE FROM score_claim WHERE id=?", (claim_id,))
            else:
                connection.execute(
                    "UPDATE score_claim SET display_term='Forged' WHERE id=?",
                    (claim_id,),
                )
            connection.execute(sql)
        with pytest.raises(sqlite3.IntegrityError, match=r"score (claim|stage)"):
            connection.execute("UPDATE score SET is_current=1 WHERE id=?", (score_id,))


def _initialize_v28(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    modules = tuple(m for m in db_session._MIGRATION_MODULES if m.VERSION < "0029")
    db_session._expected_schema.cache_clear()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(db_session, "_MIGRATION_MODULES", modules)
            database = Database(path)
            try:
                database.initialize()
            finally:
                database.dispose()
    finally:
        db_session._expected_schema.cache_clear()


def test_v29_blank_upgrade(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "blank-upgrade.db"
    _initialize_v28(path, monkeypatch)
    database = Database(path)
    database.initialize()
    database.dispose()
    with _connect(path) as connection:
        assert connection.execute(
            "SELECT version FROM schema_migration WHERE version=?", (v29.VERSION,)
        ).fetchone() == (v29.VERSION,)
        installed = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            )
        }
        assert installed.issuperset(v29.TRIGGER_NAMES)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("defect", (None, "key", "label", "missing"))
def test_v29_populated_upgrade_preserves_history_or_fails_atomically(
    scored: tuple[Any, TestClient, str, str], defect: str | None
) -> None:
    app, client, _, candidate_id = scored
    detail = client.get(f"/api/candidates/{candidate_id}").json()
    path = app.state.database.path
    with _connect(path) as connection:
        for trigger in v29.TRIGGER_NAMES:
            connection.execute(f'DROP TRIGGER "{trigger}"')
        connection.execute(
            "DELETE FROM schema_migration WHERE version=?", (v29.VERSION,)
        )
        # Model a true v28 snapshot: stage was not part of its immutable identity.
        sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='score_content_is_immutable'"
        ).fetchone()[0]
        connection.execute("DROP TRIGGER score_content_is_immutable")
        connection.execute(
            "UPDATE score SET source_snapshot="
            "json_remove(source_snapshot,'$.scoring_stage')"
        )
        for (score_id,) in connection.execute("SELECT id FROM score").fetchall():
            connection.execute(
                "UPDATE score SET input_fingerprint=? WHERE id=?",
                (hashlib.sha256(f"legacy:{score_id}".encode()).hexdigest(), score_id),
            )
        connection.execute("UPDATE score SET stage='provisional' WHERE is_current=1")
        connection.execute(sql)
        if defect:
            trigger = (
                "score_claim_no_delete"
                if defect == "missing"
                else "score_claim_is_immutable"
            )
            sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE name=?", (trigger,)
            ).fetchone()[0]
            connection.execute(f'DROP TRIGGER "{trigger}"')
            claim_id = detail["signals"][0]["claims"][0]["id"]
            if defect == "missing":
                connection.execute("DELETE FROM score_claim WHERE id=?", (claim_id,))
            elif defect == "key":
                connection.execute(
                    "UPDATE score_claim SET claim_key='S-1:forged' WHERE id=?",
                    (claim_id,),
                )
            else:
                connection.execute(
                    "UPDATE score_claim SET display_term='Forged' WHERE id=?",
                    (claim_id,),
                )
            connection.execute(sql)
        before = list(connection.iterdump())
        scores_before = connection.execute("SELECT * FROM score ORDER BY id").fetchall()
    database = Database(path)
    try:
        if defect:
            with pytest.raises(
                RuntimeError,
                match=r"immutable claim identity/set.*purge owning session",
            ):
                database.initialize()
        else:
            database.initialize()
    finally:
        database.dispose()
    with _connect(path) as connection:
        if defect:
            assert list(connection.iterdump()) == before
        else:
            assert (
                connection.execute("SELECT * FROM score ORDER BY id").fetchall()
                == scores_before
            )
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
            assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    if defect is None:
        response = client.post(f"/api/candidates/{candidate_id}/rescore")
        assert response.status_code == 200, response.json()
        assert response.json()["stage"] == "enriched"
        assert response.json()["score_id"] != detail["score"]["score_id"]
        with _connect(path) as connection:
            for old in scores_before:
                historical = connection.execute(
                    "SELECT * FROM score WHERE id=?", (old[0],)
                ).fetchone()
                assert historical[:-2] == old[:-2]


def test_each_signal_claim_display_is_canonical_and_unforgable(
    scored: tuple[Any, TestClient, str, str],
) -> None:
    app, client, session_id, candidate_id = scored
    response = client.put(
        "/api/briefs/current",
        json=_brief(
            session_id,
            optional_skills=[{"term": "Java"}],
            required_experience_months=12,
            target_titles=[{"term": "Platform Engineer"}, {"term": "Engineer"}],
            industries=[{"term": "Example"}, {"term": "Acme"}],
            location="Austin",
        ),
    )
    assert response.status_code == 200, response.json()
    detail = client.get(f"/api/candidates/{candidate_id}").json()
    assert {item["signal_id"] for item in detail["signals"]} == {
        "S-1",
        "S-2",
        "S-3",
        "S-4",
        "S-5",
        "S-6",
        "S-8",
    }
    with _connect(app.state.database.path) as connection:
        _temporarily_unstage(connection, detail["score"]["score_id"])
        trigger = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='score_claim_no_delete'"
        ).fetchone()[0]
        for signal in detail["signals"]:
            claim_id = signal["claims"][0]["id"]
            original = connection.execute(
                "SELECT * FROM score_claim WHERE id=?", (claim_id,)
            ).fetchone()
            connection.execute("DROP TRIGGER score_claim_no_delete")
            connection.execute("DELETE FROM score_claim WHERE id=?", (claim_id,))
            connection.execute(trigger)
            for label in ("Forged label", original[3] + "\x00suffix"):
                forged = (*original[:3], label, *original[4:])
                with pytest.raises(sqlite3.IntegrityError, match=r"claim.*brief"):
                    connection.execute(
                        "INSERT INTO score_claim VALUES (?,?,?,?,?,?,?,?)", forged
                    )
            connection.execute(
                "INSERT INTO score_claim VALUES (?,?,?,?,?,?,?,?)", original
            )
        connection.execute(
            "UPDATE score SET is_current=1 WHERE id=?", (detail["score"]["score_id"],)
        )


@pytest.mark.parametrize("all_inert", (False, True))
def test_enrichment_promotes_stage_with_unchanged_scoring_sections_once(
    scored: tuple[Any, TestClient, str, str], all_inert: bool
) -> None:
    app, client, session_id, candidate_id = scored
    with _connect(app.state.database.path) as connection:
        connection.execute(
            "UPDATE candidate SET stage='stage1' WHERE id=?", (candidate_id,)
        )
    response = client.put(
        "/api/briefs/current",
        json=_brief(
            session_id,
            required_credentials=[],
            positive_keywords=["platform"],
            required_skills=[] if all_inert else [{"term": "Python"}],
        ),
    )
    assert response.status_code == 200, response.json()
    before = client.get(f"/api/candidates/{candidate_id}").json()["score"]
    assert before["stage"] == "provisional"
    with _connect(app.state.database.path) as connection:
        snapshot = json.loads(
            connection.execute(
                "SELECT source_snapshot FROM score WHERE id=?", (before["score_id"],)
            ).fetchone()[0]
        )
    executor = app.state.job_queue.executor
    assert isinstance(executor, SequenceExecutor)
    for index in range(2):
        executor.responses.append(
            _profile_result(projects=f"Display-only project {index}")
        )
        job = client.post(
            f"/api/candidates/{candidate_id}/enrich", json={"sections": ["projects"]}
        ).json()
        assert _wait(app, job["job_id"]).state == "done"
        after = client.get(f"/api/candidates/{candidate_id}").json()["score"]
        assert after["stage"] == "enriched"
        assert after["score"] == before["score"]
        if index == 0:
            assert after["score_id"] != before["score_id"]
            first_enriched_id = after["score_id"]
        else:
            assert after["score_id"] == first_enriched_id
        with _connect(app.state.database.path) as connection:
            current_snapshot = json.loads(
                connection.execute(
                    "SELECT source_snapshot FROM score WHERE id=?", (after["score_id"],)
                ).fetchone()[0]
            )
            assert current_snapshot["sections"] == snapshot["sections"]
            assert current_snapshot["profile_snapshot"] == snapshot["profile_snapshot"]


@pytest.mark.parametrize("defect", ("missing", "extra", "order", "label"))
def test_persistence_rejects_forged_kernel_claims_before_writing(
    scored: tuple[Any, TestClient, str, str], defect: str
) -> None:
    app, client, _, candidate_id = scored
    score_id = client.get(f"/api/candidates/{candidate_id}").json()["score"]["score_id"]
    with app.state.database.sessions() as session:
        score = session.get(CandidateScore, score_id)
        assert score is not None
        candidate = session.get(Candidate, candidate_id)
        brief = session.get(RoleBrief, score.brief_id)
        config = session.get(ScoringConfig, score.scoring_config_id)
        assert candidate is not None and brief is not None and config is not None
        kernel_brief = scoring_persist.load_kernel_brief(session, brief)
        snapshot, sources = scoring_persist.build_snapshot(
            session, candidate, active_signal_ids(kernel_brief)
        )
        calculation = calculate_score(
            kernel_brief,
            scoring_persist.load_kernel_config(config),
            snapshot,
            stage=ScoreStage.ENRICHED,
        )
        signal = calculation.signals[0]
        claims = signal.claims
        if defect == "missing":
            object.__setattr__(signal, "claims", claims[:1])
        elif defect == "extra":
            object.__setattr__(signal, "claims", (*claims, claims[0]))
        elif defect == "order":
            object.__setattr__(signal, "claims", tuple(reversed(claims)))
        else:
            object.__setattr__(claims[0], "display_term", "Forged label")
        with pytest.raises(ValueError, match=r"claim identities.*immutable brief"):
            scoring_persist.persist_calculation(
                session,
                candidate=candidate,
                brief=brief,
                config=config,
                calculation=calculation,
                fingerprint="f" * 64,
                fingerprint_payload=score.source_snapshot,
                source_sections=sources,
            )
        assert not session.new and not session.dirty
