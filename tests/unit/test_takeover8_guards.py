from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path

import pytest
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.testclient import TestClient
from linkedin_dashboard.api._filters import preserve_provenance_text
from linkedin_dashboard.db import session as db_session
from linkedin_dashboard.db.migrations import (
    v0001_constraints,
    v0015_approved_evidence_roots,
)
from linkedin_dashboard.db.session import Database
from linkedin_dashboard.db.unicode_identity import register_sqlite_unicode_casefold
from linkedin_dashboard.main import create_app
from linkedin_dashboard.settings import Settings
from sqlalchemy.exc import DBAPIError


def _maintenance_connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    register_sqlite_unicode_casefold(connection)
    return connection


def _settings(path: Path) -> Settings:
    return Settings(db_path=path, llm_provider="null", send_enabled=False)


def _rewrite_schema(path: Path, *, table: str, old: str, new: str) -> None:
    with _maintenance_connect(path) as connection:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        assert row is not None and old in row[0]
        connection.execute("PRAGMA writable_schema=ON")
        connection.execute(
            "UPDATE sqlite_master SET sql=? WHERE type='table' AND name=?",
            (row[0].replace(old, new, 1), table),
        )
        version = connection.execute("PRAGMA schema_version").fetchone()[0]
        connection.execute(f"PRAGMA schema_version={version + 1}")


def _seed_approved_evidence_graph(
    database: Database, suffix: str, *, approve: bool = True
) -> dict[str, str]:
    identifiers = {
        name: f"{name}-{suffix}"
        for name in (
            "session",
            "candidate-a",
            "candidate-b",
            "brief",
            "score-a",
            "score-b",
            "signal-a",
            "signal-b",
            "evidence-a",
            "evidence-b",
            "draft-a",
            "draft-b",
            "claim-a",
            "confirmation",
        )
    }
    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO session "
            "(id, created_at, label, purge_after, nav_budget, nav_used, send_enabled) "
            "VALUES (?, 'now', 'M0', 'later', 120, 0, 0)",
            (identifiers["session"],),
        )
        for label in ("a", "b"):
            connection.exec_driver_sql(
                "INSERT INTO candidate "
                "(id, session_id, username, profile_url, first_seen_at, stage, "
                "retrieval_status) VALUES (?, ?, ?, ?, 'now', 'discovered', 'pending')",
                (
                    identifiers[f"candidate-{label}"],
                    identifiers["session"],
                    f"person-{label}-{suffix}",
                    f"https://www.linkedin.com/in/person-{label}-{suffix}/",
                ),
            )
        connection.exec_driver_sql(
            "INSERT INTO role_brief "
            "(id, session_id, version, created_at, job_description, target_titles, "
            "location, industries, positive_keywords, negative_keywords, "
            "message_tone, weights_version) "
            "VALUES (?, ?, 1, 'now', 'job', '[]', 'anywhere', '[]', '[]', '[]', "
            "'plain', 'v1')",
            (identifiers["brief"], identifiers["session"]),
        )
        connection.exec_driver_sql(
            "INSERT INTO scoring_config VALUES (?, ?, 1, 'now', "
            '\'{"S-1":0,"S-2":0,"S-3":0,"S-4":0,"S-5":0,'
            '"S-6":1,"S-8":0}\', \'{}\', NULL)',
            (f"config-{suffix}", identifiers["session"]),
        )
        for label in ("a", "b"):
            connection.exec_driver_sql(
                "INSERT INTO score "
                "(id, candidate_id, brief_id, weights_version, scoring_config_id, "
                "stage, score, "
                "score_lower, score_upper, confidence, confidence_band, computed_at, "
                "is_current,input_fingerprint) VALUES (?, ?, ?, '1', ?, "
                "'provisional',1,1,1,1,'high','now',0,?)",
                (
                    identifiers[f"score-{label}"],
                    identifiers[f"candidate-{label}"],
                    identifiers["brief"],
                    f"config-{suffix}",
                    (label * 64),
                ),
            )
            connection.exec_driver_sql(
                "INSERT INTO score_signal "
                "(id, score_id, signal_id, weight, verdict, raw_subscore, "
                "contribution, availability) VALUES (?, ?, 'skill', 1, 'matched', "
                "1, 1, 1)",
                (identifiers[f"signal-{label}"], identifiers[f"score-{label}"]),
            )
            connection.exec_driver_sql(
                "INSERT INTO evidence "
                "(id, score_signal_id, section_name, span_start, span_end, snippet, "
                "matcher, matched_term, polarity) VALUES (?, ?, 'experience', 0, 6, "
                "'Python', 'exact', 'Python', 'supporting')",
                (identifiers[f"evidence-{label}"], identifiers[f"signal-{label}"]),
            )
            connection.exec_driver_sql(
                "INSERT INTO message_draft "
                "(id, candidate_id, version, body, body_sha256, char_count, generator, "
                "grounding_status, grounding_report, created_at) VALUES (?, ?, 1, "
                "'Hello', ?, 5, 'manual', 'pass', '{}', 'now')",
                (
                    identifiers[f"draft-{label}"],
                    identifiers[f"candidate-{label}"],
                    label * 64,
                ),
            )
        connection.exec_driver_sql(
            "INSERT INTO draft_claim "
            "(id, draft_id, claim_text, evidence_id, grounded) "
            "VALUES (?, ?, 'Python', ?, 1)",
            (
                identifiers["claim-a"],
                identifiers["draft-a"],
                identifiers["evidence-a"],
            ),
        )
        if approve:
            connection.exec_driver_sql(
                "INSERT INTO send_confirmation "
                "(token, candidate_id, draft_id, body_sha256, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, 'now', 'later')",
                (
                    identifiers["confirmation"],
                    identifiers["candidate-a"],
                    identifiers["draft-a"],
                    "a" * 64,
                ),
            )
    return identifiers


def test_send_attempt_collation_tamper_is_rejected_on_restart(tmp_path: Path) -> None:
    path = tmp_path / "collation-tamper.db"
    database = Database(path)
    database.initialize()
    database.dispose()

    _rewrite_schema(
        path,
        table="send_attempt",
        old="state VARCHAR(32) NOT NULL",
        new="state VARCHAR(32) COLLATE NOCASE NOT NULL",
    )

    retry = Database(path)
    try:
        with pytest.raises(RuntimeError, match="required manifest"):
            retry.initialize()
    finally:
        retry.dispose()


def test_historical_text_declaration_remains_compatible(tmp_path: Path) -> None:
    path = tmp_path / "historical-text.db"
    database = Database(path)
    database.initialize()
    database.dispose()

    _rewrite_schema(
        path,
        table="send_attempt",
        old="state VARCHAR(32) NOT NULL",
        new="state TEXT NOT NULL",
    )

    retry = Database(path)
    retry.initialize()
    retry.dispose()


def test_migration_history_capability_allows_only_direct_insert_on_its_connection(
    tmp_path: Path,
) -> None:
    path = tmp_path / "connection-local-history.db"
    descriptor = db_session._open_owner_only_file(path, create=True)
    engine, exported_authority = db_session._create_migration_engine(path, descriptor)
    assert exported_authority is None
    try:
        with engine.connect() as owner, engine.connect() as peer:
            owner.exec_driver_sql(
                "CREATE TABLE schema_migration "
                "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            authority = owner.info["migration_authorizer"]
            with authority.history_write():
                owner.exec_driver_sql(
                    "INSERT INTO schema_migration VALUES ('direct', 'now')"
                )
                with pytest.raises(DBAPIError, match="not authorized"):
                    owner.exec_driver_sql(
                        "UPDATE schema_migration SET applied_at='later'"
                    )
                with pytest.raises(DBAPIError, match="not authorized"):
                    owner.exec_driver_sql("DELETE FROM schema_migration")
                with pytest.raises(DBAPIError, match="not authorized"):
                    peer.exec_driver_sql(
                        "INSERT INTO schema_migration VALUES ('peer', 'now')"
                    )
    finally:
        engine.dispose()
        os.close(descriptor)


def test_trigger_cannot_side_effect_migration_history(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "trigger-history.db"
    database = Database(path)
    original_apply = v0001_constraints.apply

    def malicious_apply(connection) -> None:
        original_apply(connection)
        connection.exec_driver_sql(
            "CREATE TRIGGER inject_history AFTER INSERT ON schema_migration "
            "BEGIN INSERT INTO schema_migration VALUES ('forged', 'now'); END"
        )

    monkeypatch.setattr(v0001_constraints, "apply", malicious_apply)
    with pytest.raises(DBAPIError, match="not authorized"):
        database.initialize()
    database.dispose()

    monkeypatch.setattr(v0001_constraints, "apply", original_apply)
    retry = Database(path)
    retry.initialize()
    retry.dispose()


def test_unexpected_migration_version_is_rejected_on_restart(tmp_path: Path) -> None:
    path = tmp_path / "forged-history.db"
    database = Database(path)
    database.initialize()
    database.dispose()
    with _maintenance_connect(path) as connection:
        connection.execute("INSERT INTO schema_migration VALUES ('9999_forged', 'now')")

    retry = Database(path)
    try:
        with pytest.raises(RuntimeError, match="migration history"):
            retry.initialize()
    finally:
        retry.dispose()


def test_duplicate_configured_migration_version_is_rejected(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        db_session,
        "_MIGRATION_MODULES",
        (v0001_constraints, v0001_constraints),
    )
    database = Database(tmp_path / "duplicate-migration-version.db")
    try:
        with pytest.raises(RuntimeError, match="migration versions must be unique"):
            database.initialize()
    finally:
        database.dispose()


@pytest.mark.parametrize("recursive_triggers", ["ON", "OFF"])
@pytest.mark.parametrize(
    "operation",
    ["update", "update_or_replace", "upsert", "replace", "collision"],
)
def test_approved_score_signal_root_survives_every_destructive_write(
    database: Database, recursive_triggers: str, operation: str
) -> None:
    ids = _seed_approved_evidence_graph(
        database, f"signal-root-{operation}-{recursive_triggers.lower()}"
    )
    path = database.path
    database.dispose()

    with _maintenance_connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA recursive_triggers={recursive_triggers}")
        with pytest.raises(
            sqlite3.IntegrityError,
            match=(
                r"approved score_signal identity and root are immutable|"
                r"M4 score signal"
            ),
        ):
            if operation == "update":
                connection.execute(
                    "UPDATE score_signal SET score_id=? WHERE id=?",
                    (ids["score-b"], ids["signal-a"]),
                )
            elif operation == "update_or_replace":
                connection.execute(
                    "UPDATE OR REPLACE score_signal SET score_id=? WHERE id=?",
                    (ids["score-b"], ids["signal-a"]),
                )
            elif operation == "upsert":
                connection.execute(
                    "INSERT INTO score_signal "
                    "(id, score_id, signal_id, weight, verdict, raw_subscore, "
                    "contribution, availability) VALUES (?, ?, 'other', 1, "
                    "'matched', 1, 1, 1) ON CONFLICT(id) DO UPDATE SET "
                    "score_id=excluded.score_id",
                    (ids["signal-a"], ids["score-b"]),
                )
            elif operation == "replace":
                connection.execute(
                    "INSERT OR REPLACE INTO score_signal "
                    "(id, score_id, signal_id, weight, verdict, raw_subscore, "
                    "contribution, availability) VALUES (?, ?, 'other', 1, "
                    "'matched', 1, 1, 1)",
                    (ids["signal-a"], ids["score-b"]),
                )
            else:
                connection.execute(
                    "UPDATE OR REPLACE score_signal SET id=? WHERE id=?",
                    (ids["signal-a"], ids["signal-b"]),
                )

        assert connection.execute(
            "SELECT score_id FROM score_signal WHERE id=?", (ids["signal-a"],)
        ).fetchone() == (ids["score-a"],)
        assert connection.execute(
            "SELECT COUNT(*) FROM evidence WHERE id=?", (ids["evidence-a"],)
        ).fetchone() == (1,)


@pytest.mark.parametrize("recursive_triggers", ["ON", "OFF"])
def test_approved_score_signal_direct_delete_is_blocked(
    database: Database, recursive_triggers: str
) -> None:
    ids = _seed_approved_evidence_graph(
        database, f"signal-delete-{recursive_triggers.lower()}"
    )
    path = database.path
    database.dispose()
    with _maintenance_connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA recursive_triggers={recursive_triggers}")
        with pytest.raises(
            sqlite3.IntegrityError, match=r"only by session purge|append-only"
        ):
            connection.execute(
                "DELETE FROM score_signal WHERE id=?", (ids["signal-a"],)
            )


@pytest.mark.parametrize("operation", ["insert", "update"])
def test_cross_candidate_draft_claim_evidence_is_rejected(
    database: Database, operation: str
) -> None:
    ids = _seed_approved_evidence_graph(database, f"cross-claim-{operation}")
    with pytest.raises(DBAPIError, match="evidence must belong to draft candidate"):
        with database.engine.begin() as connection:
            if operation == "insert":
                connection.exec_driver_sql(
                    "INSERT INTO draft_claim "
                    "(id, draft_id, claim_text, evidence_id, grounded) "
                    "VALUES ('cross-claim', ?, 'Wrong candidate', ?, 1)",
                    (ids["draft-b"], ids["evidence-a"]),
                )
            else:
                connection.exec_driver_sql(
                    "INSERT INTO draft_claim "
                    "(id, draft_id, claim_text, evidence_id, grounded) "
                    "VALUES ('mutable-claim', ?, 'Same candidate', ?, 1)",
                    (ids["draft-b"], ids["evidence-b"]),
                )
                connection.exec_driver_sql(
                    "UPDATE draft_claim SET evidence_id=? WHERE id='mutable-claim'",
                    (ids["evidence-a"],),
                )


def test_confirmation_rechecks_claim_candidate_after_draft_root_drift(
    database: Database,
) -> None:
    ids = _seed_approved_evidence_graph(database, "claim-root-drift", approve=False)
    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE message_draft SET version=2 WHERE id=?", (ids["draft-b"],)
        )
        connection.exec_driver_sql(
            "UPDATE message_draft SET candidate_id=? WHERE id=?",
            (ids["candidate-b"], ids["draft-a"]),
        )
        with pytest.raises(
            DBAPIError, match="approved draft claims must belong to recipient candidate"
        ):
            connection.exec_driver_sql(
                "INSERT INTO send_confirmation "
                "(token, candidate_id, draft_id, body_sha256, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, 'now', 'later')",
                (
                    ids["confirmation"],
                    ids["candidate-b"],
                    ids["draft-a"],
                    "a" * 64,
                ),
            )


@pytest.mark.parametrize("recursive_triggers", ["ON", "OFF"])
@pytest.mark.parametrize("state, confirm_send", [("DRY_RUN_OK", 0), ("SENT", 1)])
@pytest.mark.parametrize("insert_kind", ["plain", "replace", "upsert"])
def test_attempt_rechecks_claim_candidate_after_draft_root_drift(
    database: Database,
    recursive_triggers: str,
    state: str,
    confirm_send: int,
    insert_kind: str,
) -> None:
    suffix = f"attempt-root-drift-{recursive_triggers}-{state}-{insert_kind}"
    ids = _seed_approved_evidence_graph(database, suffix, approve=False)
    path = database.path
    database.dispose()
    insert = "INSERT OR REPLACE" if insert_kind == "replace" else "INSERT"
    upsert = (
        " ON CONFLICT(id) DO UPDATE SET state=excluded.state"
        if insert_kind == "upsert"
        else ""
    )
    attempt_id = f"attempt-{suffix}"
    with _maintenance_connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA recursive_triggers={recursive_triggers}")
        connection.execute(
            "UPDATE message_draft SET version=2 WHERE id=?", (ids["draft-b"],)
        )
        connection.execute(
            "UPDATE message_draft SET candidate_id=? WHERE id=?",
            (ids["candidate-b"], ids["draft-a"]),
        )
        with pytest.raises(
            sqlite3.IntegrityError,
            match="attempted draft claims must belong to recipient candidate",
        ):
            connection.execute(
                f"{insert} INTO send_attempt "
                "(id, candidate_id, draft_id, idempotency_key, body_sha256, "
                "confirm_send, state, started_at, finished_at, resolution) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'now', 'later', 'unresolved')"
                f"{upsert}",
                (
                    attempt_id,
                    ids["candidate-b"],
                    ids["draft-a"],
                    attempt_id.ljust(64, "0")[:64],
                    "a" * 64,
                    confirm_send,
                    state,
                ),
            )


@pytest.mark.parametrize("recursive_triggers", ["ON", "OFF"])
def test_valid_claim_attempt_allows_full_session_purge(
    database: Database, recursive_triggers: str
) -> None:
    ids = _seed_approved_evidence_graph(
        database, f"valid-attempt-purge-{recursive_triggers}", approve=False
    )
    path = database.path
    database.dispose()
    with _maintenance_connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA recursive_triggers={recursive_triggers}")
        connection.execute(
            "INSERT INTO send_attempt "
            "(id, candidate_id, draft_id, idempotency_key, body_sha256, "
            "confirm_send, state, started_at, finished_at, resolution) "
            "VALUES ('valid-attempt', ?, ?, ?, ?, 0, 'DRY_RUN_OK', "
            "'now', 'later', 'unresolved')",
            (
                ids["candidate-a"],
                ids["draft-a"],
                "valid-attempt".ljust(64, "0"),
                "a" * 64,
            ),
        )
        connection.execute("DELETE FROM session WHERE id=?", (ids["session"],))
        assert connection.execute(
            "SELECT COUNT(*) FROM send_attempt WHERE id='valid-attempt'"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT COUNT(*) FROM evidence WHERE id=?", (ids["evidence-a"],)
        ).fetchone() == (0,)


@pytest.mark.parametrize("recursive_triggers", ["ON", "OFF"])
def test_approved_evidence_ancestry_allows_full_session_purge(
    database: Database, recursive_triggers: str
) -> None:
    ids = _seed_approved_evidence_graph(
        database, f"ancestry-session-purge-{recursive_triggers.lower()}"
    )
    path = database.path
    database.dispose()
    with _maintenance_connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA recursive_triggers={recursive_triggers}")
        connection.execute("DELETE FROM session WHERE id=?", (ids["session"],))
        assert connection.execute(
            "SELECT COUNT(*) FROM score_signal WHERE id=?", (ids["signal-a"],)
        ).fetchone() == (0,)


def _schema_objects(path: Path) -> list[tuple[str, str, str]]:
    with _maintenance_connect(path) as connection:
        return connection.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE type IN ('index','trigger') AND sql IS NOT NULL "
            "ORDER BY type, name"
        ).fetchall()


@pytest.mark.parametrize(
    "failure_after", range(1, len(v0015_approved_evidence_roots.STATEMENTS) + 1)
)
def test_v0015_each_statement_is_atomic_and_retryable(
    tmp_path: Path, monkeypatch, failure_after: int
) -> None:
    path = tmp_path / f"interrupted-v15-{failure_after}.db"
    database = Database(path)
    database.initialize()
    database.dispose()
    with _maintenance_connect(path) as connection:
        connection.execute(
            "DELETE FROM schema_migration WHERE version=?",
            (v0015_approved_evidence_roots.VERSION,),
        )
        for name in v0015_approved_evidence_roots.TRIGGER_NAMES:
            connection.execute(f'DROP TRIGGER "{name}"')
    baseline = _schema_objects(path)
    retry = Database(path)
    original_apply = v0015_approved_evidence_roots.apply

    def interrupted_apply(connection) -> None:
        assert not connection.exec_driver_sql(
            v0015_approved_evidence_roots._PREFLIGHT
        ).scalar_one()
        for index, statement in enumerate(
            v0015_approved_evidence_roots.STATEMENTS, start=1
        ):
            connection.exec_driver_sql(statement)
            if index == failure_after:
                raise RuntimeError(f"interrupted after v15 statement {index}")

    monkeypatch.setattr(v0015_approved_evidence_roots, "apply", interrupted_apply)
    with pytest.raises(RuntimeError, match=f"v15 statement {failure_after}"):
        retry.initialize()
    assert _schema_objects(path) == baseline

    monkeypatch.setattr(v0015_approved_evidence_roots, "apply", original_apply)
    retry.initialize()
    retry.dispose()


def test_v0015_preflight_rejects_existing_cross_candidate_claim(
    tmp_path: Path,
) -> None:
    path = tmp_path / "v15-preflight.db"
    database = Database(path)
    database.initialize()
    ids = _seed_approved_evidence_graph(database, "v15-preflight")
    database.dispose()
    with _maintenance_connect(path) as connection:
        for name in v0015_approved_evidence_roots.TRIGGER_NAMES:
            connection.execute(f'DROP TRIGGER "{name}"')
        connection.execute(
            "DELETE FROM schema_migration WHERE version=?",
            (v0015_approved_evidence_roots.VERSION,),
        )
        connection.execute(
            "INSERT INTO draft_claim "
            "(id, draft_id, claim_text, evidence_id, grounded) "
            "VALUES ('legacy-cross-claim', ?, 'Wrong candidate', ?, 1)",
            (ids["draft-b"], ids["evidence-a"]),
        )

    retry = Database(path)
    try:
        with pytest.raises(RuntimeError, match="cross-candidate draft_claim"):
            retry.initialize()
    finally:
        retry.dispose()


def test_v0015_preflight_rejects_existing_moved_draft_attempt(
    tmp_path: Path,
) -> None:
    path = tmp_path / "v15-moved-draft-attempt-preflight.db"
    database = Database(path)
    database.initialize()
    ids = _seed_approved_evidence_graph(database, "v15-moved-attempt", approve=False)
    database.dispose()
    with _maintenance_connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        for name in v0015_approved_evidence_roots.TRIGGER_NAMES:
            connection.execute(f'DROP TRIGGER "{name}"')
        connection.execute(
            "DELETE FROM schema_migration WHERE version=?",
            (v0015_approved_evidence_roots.VERSION,),
        )
        connection.execute(
            "UPDATE message_draft SET version=2 WHERE id=?", (ids["draft-b"],)
        )
        connection.execute(
            "UPDATE message_draft SET candidate_id=? WHERE id=?",
            (ids["candidate-b"], ids["draft-a"]),
        )
        connection.execute(
            "INSERT INTO send_attempt "
            "(id, candidate_id, draft_id, idempotency_key, body_sha256, "
            "confirm_send, state, started_at, finished_at, resolution) "
            "VALUES ('legacy-moved-attempt', ?, ?, ?, ?, 0, 'DRY_RUN_OK', "
            "'now', 'later', 'unresolved')",
            (
                ids["candidate-b"],
                ids["draft-a"],
                "legacy-moved-attempt".ljust(64, "0")[:64],
                "a" * 64,
            ),
        )

    retry = Database(path)
    try:
        with pytest.raises(RuntimeError, match="cross-candidate draft_claim"):
            retry.initialize()
    finally:
        retry.dispose()


def test_two_database_instances_concurrently_bootstrap_once(tmp_path: Path) -> None:
    path = tmp_path / "concurrent-bootstrap.db"
    databases = [Database(path), Database(path)]
    barrier = threading.Barrier(3)
    failures: list[BaseException] = []

    def initialize(database: Database) -> None:
        barrier.wait()
        try:
            database.initialize()
        except BaseException as error:  # pragma: no cover - asserted below
            failures.append(error)

    workers = [
        threading.Thread(target=initialize, args=(database,)) for database in databases
    ]
    for worker in workers:
        worker.start()
    barrier.wait()
    for worker in workers:
        worker.join(timeout=30)

    assert all(not worker.is_alive() for worker in workers)
    assert failures == []
    for database in databases:
        with database.engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT COUNT(*) FROM schema_migration"
            ).scalar_one() == len(db_session._MIGRATION_MODULES)
        database.dispose()


def test_bootstrap_state_is_read_only_after_write_transaction_is_owned(
    tmp_path: Path, monkeypatch
) -> None:
    original_probe = db_session._database_has_no_user_schema
    observed: list[bool] = []

    def assert_owned(connection) -> bool:
        observed.append(connection.in_transaction())
        return original_probe(connection)

    monkeypatch.setattr(db_session, "_database_has_no_user_schema", assert_owned)
    database = Database(tmp_path / "transaction-owned-bootstrap.db")
    database.initialize()
    database.dispose()

    assert observed == [True]


def test_provenance_requires_explicit_handler_marker_and_still_redacts_secrets(
    tmp_path: Path,
) -> None:
    app = create_app(_settings(tmp_path / "provenance-marker.db"))
    prose = (
        "Authentication: OAuth 2.0 / Bearer tokens\n"
        "Bearer token validation uses /api/v1 and /health\n"
        "Key: Kubernetes"
    )
    private = (
        "/mnt/Recruiter Data/Browser Profile; "
        r"C:\Recruiter Data\Browser Profile"
        "; Authorization: Bearer actual-secret-token"
    )
    payload = {
        "raw_text": prose,
        "sections": {
            "main_profile": prose,
            "private": private,
            "notes": "Authentication: OAuth 2.0 appended-secret",
        },
        "signals": [{"evidence": [{"snippet": prose}]}],
        "detail": {
            "raw_text": prose,
            "sections": {"main_profile": prose},
            "signals": [{"evidence": [{"snippet": prose}]}],
        },
    }

    @app.get("/api/test/owned-provenance")
    @preserve_provenance_text
    def owned_json() -> JSONResponse:
        return JSONResponse(payload)

    @app.get("/api/test/spoof-provenance")
    def spoof_json() -> JSONResponse:
        return JSONResponse(payload)

    event = ("data: " + json.dumps(payload) + "\n\n").encode()

    @app.get("/api/test/owned-provenance-sse")
    @preserve_provenance_text
    def owned_sse() -> StreamingResponse:
        return StreamingResponse(
            (bytes([byte]) for byte in event), media_type="text/event-stream"
        )

    with TestClient(app, base_url="http://127.0.0.1") as client:
        owned = client.get("/api/test/owned-provenance")
        spoof = client.get("/api/test/spoof-provenance")
        stream = client.get("/api/test/owned-provenance-sse")

    owned_payload = owned.json()
    assert owned_payload["raw_text"] == prose
    assert owned_payload["sections"]["main_profile"] == prose
    assert owned_payload["signals"][0]["evidence"][0]["snippet"] == prose
    assert owned_payload["detail"]["raw_text"] != prose
    assert owned_payload["detail"]["sections"]["main_profile"] != prose
    assert owned_payload["detail"]["signals"][0]["evidence"][0]["snippet"] != prose
    assert spoof.json()["sections"]["main_profile"] != prose
    stream_payload = json.loads(
        "\n".join(
            line.removeprefix("data: ")
            for line in stream.text.splitlines()
            if line.startswith("data:")
        )
    )
    assert stream_payload["raw_text"] == prose
    assert stream_payload["signals"][0]["evidence"][0]["snippet"] == prose
    for start, end in (
        (prose.index("/api/v1"), prose.index("/api/v1") + len("/api/v1")),
        (prose.index("/health"), prose.index("/health") + len("/health")),
        (
            prose.index("Bearer token validation"),
            prose.index("Bearer token validation") + len("Bearer token validation"),
        ),
    ):
        assert owned_payload["raw_text"][start:end] == prose[start:end]
        assert stream_payload["raw_text"][start:end] == prose[start:end]

    for output in (owned.text, stream.text):
        assert "/mnt/Recruiter Data" not in output
        assert r"C:\Recruiter Data" not in output
        assert "actual-secret-token" not in output
        assert "appended-secret" not in output
        assert "Authentication: OAuth 2.0" in output
        assert "Bearer tokens" in output
        assert "Bearer token validation" in output
        assert "/api/v1" in output
        assert "/health" in output
        assert "Key: Kubernetes" in output


def test_provenance_redacts_sensitive_labels_before_benign_allowlist(
    tmp_path: Path,
) -> None:
    app = create_app(_settings(tmp_path / "provenance-label-first.db"))
    source = (
        "/api/v1\n"
        "see Bearer token validation docs\n"
        "Key: Kubernetes secrets store\n"
        "cookie_path=/api/v1\n"
        "api_key=Bearer tokens\n"
        "authorization=Bearer token validation\n"
        "credential=Key: Kubernetes\n"
        "source_profile_dir=/health\n"
        "user=alice credential=Key: Kubernetes region=us\n"
        "prefix cookie_path=/api/v1, suffix=visible"
    )
    mask = lambda value: "█" * len(value)  # noqa: E731 - expected shape helper
    expected = (
        "/api/v1\n"
        "see Bearer token validation docs\n"
        "Key: Kubernetes secrets store\n"
        f"cookie_path={mask('/api/v1')}\n"
        f"api_key={mask('Bearer tokens')}\n"
        f"authorization={mask('Bearer token validation')}\n"
        f"credential={mask('Key: Kubernetes')}\n"
        f"source_profile_dir={mask('/health')}\n"
        f"user=alice credential={mask('Key: Kubernetes')} region=us\n"
        f"prefix cookie_path={mask('/api/v1')}, suffix=visible"
    )

    @app.get("/api/test/label-first-provenance")
    @preserve_provenance_text
    def marked_json() -> JSONResponse:
        return JSONResponse({"sections": {"main_profile": source}})

    event = (
        "data: " + json.dumps({"sections": {"main_profile": source}}) + "\n\n"
    ).encode()

    @app.get("/api/test/label-first-provenance-sse")
    @preserve_provenance_text
    def marked_sse() -> StreamingResponse:
        return StreamingResponse(
            (bytes([byte]) for byte in event), media_type="text/event-stream"
        )

    with TestClient(app, base_url="http://127.0.0.1") as client:
        json_response = client.get("/api/test/label-first-provenance")
        sse_response = client.get("/api/test/label-first-provenance-sse")

    sse_payload = json.loads(
        "\n".join(
            line.removeprefix("data: ")
            for line in sse_response.text.splitlines()
            if line.startswith("data:")
        )
    )
    assert json_response.json()["sections"]["main_profile"] == expected
    assert sse_payload["sections"]["main_profile"] == expected


def test_arbitrary_spaced_paths_redact_in_json_sse_and_headers(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path / "arbitrary-paths.db"))
    paths = (
        "prefix /Top Secret; /one component.txt; "
        "/Volumes/Recruiting Drive/Profile Cache; "
        "suffix /mnt/Recruiting Drive/Profile Cache| "
        r"tail D:\Recruiting Drive\Profile Cache; "
        r"\\workstation\Private Operator\Browser Profile"
    )

    @app.get("/api/test/arbitrary-paths")
    def json_paths() -> JSONResponse:
        return JSONResponse({"diagnostic": paths}, headers={"X-Diagnostic": paths})

    event = ("data: " + json.dumps({"diagnostic": paths}) + "\n\n").encode()

    @app.get("/api/test/arbitrary-paths-sse")
    def sse_paths() -> StreamingResponse:
        return StreamingResponse(
            (bytes([byte]) for byte in event), media_type="text/event-stream"
        )

    with TestClient(app, base_url="http://127.0.0.1") as client:
        json_response = client.get("/api/test/arbitrary-paths")
        sse_response = client.get("/api/test/arbitrary-paths-sse")

    for output in (
        json_response.text,
        sse_response.text,
        json_response.headers["x-diagnostic"],
    ):
        assert "Recruiting Drive" not in output
        assert "Profile Cache" not in output
        assert "/Volumes" not in output
        assert "/mnt" not in output
        assert "/Top" not in output
        assert "/one" not in output
        assert "Top Secret" not in output
        assert "one component.txt" not in output
        assert "[redacted-path] Secret" not in output
        assert "[redacted-path] component.txt" not in output
        assert "workstation" not in output
        assert "prefix" in output
        assert "suffix" in output


def test_colon_and_path_delimited_url_credentials_are_redacted_everywhere(
    tmp_path: Path,
) -> None:
    app = create_app(_settings(tmp_path / "url-components.db"))
    credential_urls = [
        "https://example.test/client_secret/path-secret/safe",
        "https://example.test/safe/client_secret:path-colon-secret/rest",
        "https://example.test/safe?client_secret:query-secret&view=public",
        "https://example.test/safe#client_secret:fragment-secret",
    ]
    safe_url = "https://example.test/api/v1?view=public#section:overview"
    payload = {"urls": [*credential_urls, safe_url]}

    @app.get("/api/test/url-components")
    def json_urls() -> JSONResponse:
        return JSONResponse(
            payload,
            headers={"X-Safe-URL": safe_url, "X-Private-URL": credential_urls[1]},
        )

    event = ("data: " + json.dumps(payload) + "\n\n").encode()

    @app.get("/api/test/url-components-sse")
    def sse_urls() -> StreamingResponse:
        return StreamingResponse(
            (bytes([byte]) for byte in event), media_type="text/event-stream"
        )

    with TestClient(app, base_url="http://127.0.0.1") as client:
        json_response = client.get("/api/test/url-components")
        sse_response = client.get("/api/test/url-components-sse")

    for output in (
        json_response.text,
        sse_response.text,
        json_response.headers["x-private-url"],
    ):
        assert "path-secret" not in output
        assert "path-colon-secret" not in output
        assert "query-secret" not in output
        assert "fragment-secret" not in output
    assert safe_url in json_response.text
    assert safe_url in sse_response.text
    assert json_response.headers["x-safe-url"] == safe_url
