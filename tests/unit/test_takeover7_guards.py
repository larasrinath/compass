from __future__ import annotations

import json
import re
import sqlite3
import threading
from pathlib import Path

import pytest
from fastapi import Response
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.testclient import TestClient
from linkedin_dashboard.api._filters import (
    preserve_provenance_text,
    sanitize_for_frontend,
)
from linkedin_dashboard.db.migrations import v0001_constraints
from linkedin_dashboard.db.models import DashboardSession
from linkedin_dashboard.db.session import (
    Database,
    _expected_schema,
    _normalized_schema_sql,
)
from linkedin_dashboard.main import create_app
from linkedin_dashboard.settings import Settings
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError


def settings_for(path: Path) -> Settings:
    return Settings(db_path=path, llm_provider="null", send_enabled=False)


def client_for(app) -> TestClient:
    return TestClient(app, base_url="http://127.0.0.1")


_INVARIANT_OBJECTS = sorted(
    (kind, name)
    for kind, name, _, _ in _expected_schema()[0]
    if kind in {"trigger", "index"}
)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/mcp;mode=unsafe",
        "http://127.0.0.1:8000/mcp?token=secret",
        "http://127.0.0.1:8000/mcp#fragment",
        "http://127.0.0.1:8000/other",
        "https://127.0.0.1:8000/mcp",
    ],
)
def test_mcp_url_is_only_the_direct_unauthenticated_endpoint(
    tmp_path: Path, url: str
) -> None:
    with pytest.raises(ValidationError, match=r"direct|parameters|endpoint"):
        Settings(mcp_url=url, db_path=tmp_path / "settings.db")


def test_separator_independent_diagnostic_keys_drop_without_false_positives() -> None:
    assert sanitize_for_frontend(
        {
            "client/key": "secret",
            "runtime/path": "secret",
            "X-Runtime!Path": "secret",
            "x-monkey": "banana",
            "x-keynote": "speech",
        }
    ) == {"x-monkey": "banana", "x-keynote": "speech"}


def test_spaced_paths_and_decoded_credential_url_paths_are_redacted_everywhere(
    tmp_path: Path,
) -> None:
    app = create_app(settings_for(tmp_path / "spaced-paths.db"))
    unix_path = "/Users/Jane Doe/Library/Application Support/runtime.json"
    windows_path = r"C:\Users\Jane Doe\AppData\Local\runtime.json"
    encoded_url = (
        "http%3A%2F%2Foperator%3Asecret%40127.0.0.1%3A8000"
        "%2FUsers%2FJane%2520Doe%2F.linkedin-mcp%2Fcookies.json"
    )
    payload = {"unix": unix_path, "windows": windows_path, "url": encoded_url}

    @app.get("/api/test/takeover7-json")
    def takeover7_json() -> JSONResponse:
        return JSONResponse(payload, headers={"X-Diagnostic": unix_path})

    @app.get("/api/test/takeover7-sse")
    def takeover7_sse() -> StreamingResponse:
        return StreamingResponse(
            [f"data: {payload!r}\n\n"], media_type="text/event-stream"
        )

    with client_for(app) as client:
        json_response = client.get("/api/test/takeover7-json")
        sse_response = client.get("/api/test/takeover7-sse")

    direct = sanitize_for_frontend(payload)
    for output in (
        json_response.text,
        sse_response.text,
        json_response.headers["x-diagnostic"],
        str(direct),
    ):
        assert "Jane Doe" not in output
        assert "Application Support" not in output
        assert "operator" not in output
        assert ".linkedin-mcp" not in output


def test_dotless_spaced_directory_paths_redact_through_safe_delimiters(
    tmp_path: Path,
) -> None:
    app = create_app(settings_for(tmp_path / "dotless-spaced-paths.db"))
    unix = "/Users/Jane Doe/Library/Application Support"
    windows = r"C:\Users\Jane Doe\AppData\Local"
    value = f"before {unix}; middle {windows}| after"

    @app.get("/api/test/r16-paths")
    def paths() -> JSONResponse:
        return JSONResponse({"diagnostic": value}, headers={"X-Diagnostic": value})

    event = ("data: " + json.dumps({"diagnostic": value}) + "\n\n").encode()

    @app.get("/api/test/r16-paths-sse")
    def paths_sse() -> StreamingResponse:
        return StreamingResponse(
            (bytes([byte]) for byte in event), media_type="text/event-stream"
        )

    with client_for(app) as client:
        json_response = client.get("/api/test/r16-paths")
        sse_response = client.get("/api/test/r16-paths-sse")

    for output in (
        json_response.text,
        sse_response.text,
        json_response.headers["x-diagnostic"],
    ):
        assert "Jane Doe" not in output
        assert "Application Support" not in output
        assert "AppData" not in output
        assert "before" in output
        assert "after" in output


def test_provenance_text_is_byte_exact_only_on_middleware_owned_routes(
    tmp_path: Path,
) -> None:
    app = create_app(settings_for(tmp_path / "provenance-context.db"))
    prose = "Authentication: OAuth 2.0 / Bearer tokens\nKey: Kubernetes"
    diagnostics = {
        "runtime": {"path": "/Users/Private Operator/runtime"},
        "error_message": "/Users/Private Operator/runtime failed",
    }
    payload = {
        "sections": {"main_profile": prose},
        "evidence": [{"snippet": prose}],
        "section_errors": {"main_profile": diagnostics},
    }

    @app.get("/api/candidates/r16/sections/main_profile")
    @preserve_provenance_text
    def trusted_json() -> JSONResponse:
        return JSONResponse(payload)

    @app.get("/api/candidates/r16-sse/sections/main_profile")
    @preserve_provenance_text
    def trusted_sse() -> StreamingResponse:
        event = ("data: " + json.dumps(payload) + "\n\n").encode()
        return StreamingResponse(
            (bytes([byte]) for byte in event), media_type="text/event-stream"
        )

    @app.get("/api/test/spoofed-provenance")
    def untrusted_json() -> JSONResponse:
        return JSONResponse(payload)

    with client_for(app) as client:
        trusted = client.get("/api/candidates/r16/sections/main_profile").json()
        sse = client.get("/api/candidates/r16-sse/sections/main_profile").text
        untrusted = client.get("/api/test/spoofed-provenance").json()

    assert trusted["sections"]["main_profile"] == prose
    assert trusted["evidence"][0]["snippet"] == prose
    sse_payload = json.loads(
        "\n".join(
            line.removeprefix("data: ")
            for line in sse.splitlines()
            if line.startswith("data:")
        )
    )
    assert sse_payload["sections"]["main_profile"] == prose
    assert "runtime" not in trusted["section_errors"]["main_profile"]
    assert "Private Operator" not in json.dumps(trusted["section_errors"])
    assert untrusted["sections"]["main_profile"] != prose
    assert "Kubernetes" not in untrusted["sections"]["main_profile"]


@pytest.mark.parametrize(
    ("method", "status", "expected_length"),
    [
        ("HEAD", 200, str(len(b'{"private":"body"}'))),
        ("GET", 204, None),
        ("GET", 205, "0"),
        ("GET", 304, None),
    ],
)
def test_bodyless_statuses_have_status_correct_content_length(
    tmp_path: Path, method: str, status: int, expected_length: str | None
) -> None:
    app = create_app(settings_for(tmp_path / f"bodyless-{method}-{status}.db"))

    @app.api_route("/api/test/takeover7-bodyless", methods=[method])
    def bodyless() -> Response:
        return Response(
            b'{"private":"body"}',
            status_code=status,
            media_type="application/json",
        )

    with client_for(app) as client:
        response = client.request(method, "/api/test/takeover7-bodyless")

    assert response.content == b""
    assert response.headers.get("content-length") == expected_length


def test_runtime_sql_cannot_dismantle_schema_or_integrity_guards(
    database: Database,
) -> None:
    with database.engine.connect() as connection:
        for sql in (
            "DROP TRIGGER audit_log_no_update",
            "CREATE TABLE attacker(value TEXT)",
            "ATTACH DATABASE ':memory:' AS attacker",
            "PRAGMA writable_schema=ON",
            "PRAGMA ignore_check_constraints=ON",
            "PRAGMA trusted_schema=ON",
            "PRAGMA synchronous=OFF",
            "PRAGMA foreign_keys=OFF",
            "DELETE FROM schema_migration",
        ):
            with pytest.raises(DBAPIError, match="not authorized"):
                connection.exec_driver_sql(sql)


def test_normal_runtime_database_operations_remain_allowed(database: Database) -> None:
    with database.sessions.begin() as session:
        session.add(
            DashboardSession(
                id="normal-session",
                created_at="2026-09-03T00:00:00Z",
                label="normal",
                purge_after="2026-09-04T00:00:00Z",
                nav_budget=1,
                nav_used=0,
                send_enabled=False,
            )
        )
    with database.engine.connect() as connection:
        assert (
            connection.exec_driver_sql(
                "SELECT label FROM session WHERE id='normal-session'"
            ).scalar_one()
            == "normal"
        )


def test_checkout_restores_full_synchronous_and_reinstalls_authorizer(
    database: Database,
) -> None:
    raw = database.engine.raw_connection()
    try:
        driver = raw.driver_connection
        assert driver is not None
        driver.set_authorizer(None)
        driver.execute("PRAGMA synchronous=OFF")
        assert driver.execute("PRAGMA synchronous").fetchone() == (0,)
    finally:
        raw.close()

    with database.engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA synchronous").scalar_one() == 2
        with pytest.raises(DBAPIError, match="not authorized"):
            connection.exec_driver_sql("PRAGMA synchronous=OFF")


@pytest.mark.parametrize("tamper", ["drop", "rewrite"])
def test_restart_rejects_missing_or_rewritten_guard_trigger(
    tmp_path: Path, tamper: str
) -> None:
    path = tmp_path / f"trigger-{tamper}.db"
    database = Database(path)
    database.initialize()
    database.dispose()

    with sqlite3.connect(path) as connection:
        if tamper == "drop":
            connection.execute("DROP TRIGGER audit_log_no_update")
        else:
            connection.execute("PRAGMA writable_schema=ON")
            connection.execute(
                "UPDATE sqlite_master SET sql='CREATE TRIGGER audit_log_no_update "
                "BEFORE UPDATE ON audit_log BEGIN SELECT 1; END' "
                "WHERE type='trigger' AND name='audit_log_no_update'"
            )

    retry = Database(path)
    try:
        with pytest.raises(RuntimeError, match="required manifest"):
            retry.initialize()
    finally:
        retry.dispose()


def test_restart_rejects_rows_that_bypass_check_constraints(tmp_path: Path) -> None:
    path = tmp_path / "invalid-check.db"
    database = Database(path)
    database.initialize()
    database.dispose()

    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(
            "INSERT INTO candidate "
            "(id,session_id,username,profile_url,display_name,profile_urn,"
            "first_seen_at,stage,retrieval_status) VALUES "
            "('bad','missing-session','bad','https://example.test',NULL,NULL,"
            "'now','invalid-stage','pending')"
        )

    retry = Database(path)
    try:
        with pytest.raises(RuntimeError, match="integrity check"):
            retry.initialize()
    finally:
        retry.dispose()


def _mutate_first_literal(sql: str) -> str:
    match = re.search(r"'(?P<literal>(?:''|[^'])*)'", sql)
    if match is not None:
        literal = match.group("literal")
        mutated = literal.swapcase()
        if mutated == literal:
            mutated = f"{literal}tampered"
        return sql[: match.start("literal")] + mutated + sql[match.end("literal") :]
    # Covering indexes need no string literal. Mutating their first indexed
    # column exercises the same semantic-schema rejection path.
    column = re.search(r"\((?P<column>[a-z_][a-z0-9_]*)", sql, re.IGNORECASE)
    assert column is not None
    replacement = "id" if column.group("column").casefold() != "id" else "kind"
    return sql[: column.start("column")] + replacement + sql[column.end("column") :]


@pytest.mark.parametrize(("kind", "name"), _INVARIANT_OBJECTS)
def test_restart_rejects_semantic_tamper_of_every_invariant_object(
    tmp_path: Path, kind: str, name: str
) -> None:
    path = tmp_path / f"tamper-{kind}-{name}.db"
    database = Database(path)
    database.initialize()
    database.dispose()

    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type=? AND name=?", (kind, name)
        ).fetchone()
        assert row is not None
        changed = _mutate_first_literal(row[0])
        assert _normalized_schema_sql(changed) != _normalized_schema_sql(row[0])
        connection.execute("PRAGMA writable_schema=ON")
        connection.execute(
            "UPDATE sqlite_master SET sql=? WHERE type=? AND name=?",
            (changed, kind, name),
        )
        schema_version = connection.execute("PRAGMA schema_version").fetchone()[0]
        connection.execute(f"PRAGMA schema_version={schema_version + 1}")

    retry = Database(path)
    try:
        with pytest.raises(RuntimeError, match="required manifest"):
            retry.initialize()
    finally:
        retry.dispose()


def test_schema_canonicalization_preserves_quoted_literal_semantics() -> None:
    assert _normalized_schema_sql("SELECT  1\n WHERE value = 'Send Unavailable'") == (
        "SELECT 1 WHERE value = 'Send Unavailable'"
    )
    assert _normalized_schema_sql("SELECT 1 WHERE value='Sent'") != (
        _normalized_schema_sql("SELECT 1 WHERE value='SENT'")
    )
    assert _normalized_schema_sql("SELECT 1 WHERE value='two  spaces'") != (
        _normalized_schema_sql("SELECT 1 WHERE value='two spaces'")
    )


def test_runtime_pool_is_unavailable_while_dedicated_migration_is_paused(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "paused-migration.db"
    database = Database(path)
    entered = threading.Event()
    release = threading.Event()
    failures: list[BaseException] = []
    original_apply = v0001_constraints.apply

    def paused_apply(connection) -> None:
        entered.set()
        assert release.wait(timeout=10)
        original_apply(connection)

    def initialize() -> None:
        try:
            database.initialize()
        except BaseException as error:  # pragma: no cover - assertion reports value
            failures.append(error)

    monkeypatch.setattr(v0001_constraints, "apply", paused_apply)
    worker = threading.Thread(target=initialize)
    worker.start()
    assert entered.wait(timeout=10)

    for sql in (
        "PRAGMA foreign_keys=OFF",
        "PRAGMA writable_schema=ON",
        "DELETE FROM schema_migration",
        "CREATE TABLE orphan(value TEXT)",
    ):
        with pytest.raises(RuntimeError, match="unavailable during initialization"):
            with database.engine.begin() as connection:
                connection.exec_driver_sql(sql)

    release.set()
    worker.join(timeout=10)
    assert not worker.is_alive()
    assert failures == []
    with database.engine.connect() as connection:
        assert (
            connection.exec_driver_sql(
                "SELECT COUNT(*) FROM sqlite_master WHERE name='orphan'"
            ).scalar_one()
            == 0
        )
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
    database.dispose()


def test_interrupted_first_bootstrap_header_retries_but_unknown_db_does_not(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "interrupted-bootstrap.db"
    database = Database(path)
    original_apply = v0001_constraints.apply

    def unsafe_migration(connection) -> None:
        connection.exec_driver_sql("PRAGMA writable_schema=ON")

    monkeypatch.setattr(v0001_constraints, "apply", unsafe_migration)
    with pytest.raises(DBAPIError, match="not authorized"):
        database.initialize()
    assert path.read_bytes().startswith(b"SQLite format 3\x00")

    monkeypatch.setattr(v0001_constraints, "apply", original_apply)
    database.initialize()
    database.dispose()

    unknown_path = tmp_path / "unknown.db"
    with sqlite3.connect(unknown_path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE foreign_data(value TEXT)")
    unknown = Database(unknown_path)
    try:
        with pytest.raises(RuntimeError, match="no migration history"):
            unknown.initialize()
    finally:
        unknown.dispose()


@pytest.mark.parametrize(
    "forbidden_sql",
    ["PRAGMA writable_schema=ON", "DELETE FROM schema_migration"],
)
def test_migration_schema_authority_excludes_unsafe_and_history_privileges(
    tmp_path: Path, monkeypatch, forbidden_sql: str
) -> None:
    database = Database(tmp_path / f"split-capability-{len(forbidden_sql)}.db")
    original_apply = v0001_constraints.apply

    def overreaching_migration(connection) -> None:
        connection.exec_driver_sql(forbidden_sql)

    monkeypatch.setattr(v0001_constraints, "apply", overreaching_migration)
    with pytest.raises(DBAPIError, match="not authorized"):
        database.initialize()

    monkeypatch.setattr(v0001_constraints, "apply", original_apply)
    database.initialize()
    database.dispose()
