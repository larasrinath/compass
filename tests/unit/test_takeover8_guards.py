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
from linkedin_dashboard.db.migrations import v0001_constraints
from linkedin_dashboard.db.session import Database
from linkedin_dashboard.main import create_app
from linkedin_dashboard.settings import Settings
from sqlalchemy.exc import DBAPIError


def _settings(path: Path) -> Settings:
    return Settings(db_path=path, llm_provider="null", send_enabled=False)


def _rewrite_schema(path: Path, *, table: str, old: str, new: str) -> None:
    with sqlite3.connect(path) as connection:
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
    with sqlite3.connect(path) as connection:
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
    prose = "Authentication: OAuth 2.0 / Bearer tokens\nKey: Kubernetes"
    private = (
        "/mnt/Recruiter Data/Browser Profile; "
        r"C:\Recruiter Data\Browser Profile"
        "; Authorization: Bearer actual-secret-token"
    )
    payload = {
        "sections": {
            "main_profile": prose,
            "private": private,
            "notes": "Authentication: OAuth 2.0 appended-secret",
        },
        "detail": {"sections": {"main_profile": prose}},
    }

    @app.get("/api/candidates/owned/sections/main_profile")
    @preserve_provenance_text
    def owned_json() -> JSONResponse:
        return JSONResponse(payload)

    @app.get("/api/candidates/spoof/sections/main_profile")
    def spoof_json() -> JSONResponse:
        return JSONResponse(payload)

    event = ("data: " + json.dumps(payload) + "\n\n").encode()

    @app.get("/api/candidates/owned-sse/sections/main_profile")
    @preserve_provenance_text
    def owned_sse() -> StreamingResponse:
        return StreamingResponse(
            (bytes([byte]) for byte in event), media_type="text/event-stream"
        )

    with TestClient(app, base_url="http://127.0.0.1") as client:
        owned = client.get("/api/candidates/owned/sections/main_profile")
        spoof = client.get("/api/candidates/spoof/sections/main_profile")
        stream = client.get("/api/candidates/owned-sse/sections/main_profile")

    assert owned.json()["sections"]["main_profile"] == prose
    assert owned.json()["detail"]["sections"]["main_profile"] != prose
    assert spoof.json()["sections"]["main_profile"] != prose
    for output in (owned.text, stream.text):
        assert "/mnt/Recruiter Data" not in output
        assert r"C:\Recruiter Data" not in output
        assert "actual-secret-token" not in output
        assert "appended-secret" not in output
        assert "Authentication: OAuth 2.0" in output
        assert "Bearer tokens" in output
        assert "Key: Kubernetes" in output


def test_arbitrary_spaced_paths_redact_in_json_sse_and_headers(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path / "arbitrary-paths.db"))
    paths = (
        "prefix /Volumes/Recruiting Drive/Profile Cache; "
        "suffix /mnt/Recruiting Drive/Profile Cache| "
        r"tail D:\Recruiting Drive\Profile Cache"
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
        assert "prefix" in output
        assert "suffix" in output
