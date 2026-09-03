from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import Response
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.testclient import TestClient
from linkedin_dashboard.api._filters import sanitize_for_frontend
from linkedin_dashboard.db.models import DashboardSession
from linkedin_dashboard.db.session import Database
from linkedin_dashboard.main import create_app
from linkedin_dashboard.settings import Settings
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError


def settings_for(path: Path) -> Settings:
    return Settings(db_path=path, llm_provider="null", send_enabled=False)


def client_for(app) -> TestClient:
    return TestClient(app, base_url="http://127.0.0.1")


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
            "INSERT INTO candidate VALUES "
            "('bad','missing-session','bad','https://example.test',NULL,NULL,"
            "'now','invalid-stage','pending')"
        )

    retry = Database(path)
    try:
        with pytest.raises(RuntimeError, match="integrity check"):
            retry.initialize()
    finally:
        retry.dispose()
