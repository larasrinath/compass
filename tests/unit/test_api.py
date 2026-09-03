from __future__ import annotations

from collections.abc import Iterator
from itertools import product
from pathlib import Path

import pytest
from fastapi import Response
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.testclient import TestClient
from linkedin_dashboard.api._filters import (
    _SSEParser,
    _strict_json_dumps,
    sanitize_for_frontend,
)
from linkedin_dashboard.audit import append_audit_event
from linkedin_dashboard.db.models import DashboardSession
from linkedin_dashboard.main import create_app
from linkedin_dashboard.settings import Settings


def settings_for(path: Path) -> Settings:
    return Settings(db_path=path, llm_provider="null", send_enabled=False)


def client_for(app, base_url: str = "http://127.0.0.1") -> TestClient:
    return TestClient(app, base_url=base_url)


def test_health_smoke_and_correlation_id(tmp_path) -> None:
    app = create_app(settings_for(tmp_path / "health.db"))
    with client_for(app) as client:
        response = client.get(
            "/api/health", headers={"X-Correlation-ID": "test-correlation"}
        )

    assert response.status_code == 200
    assert response.headers["x-correlation-id"] == "test-correlation"
    assert response.json() == {
        "status": "ok",
        "database": "ok",
        "send_enabled": False,
        "llm_provider": "null",
    }


def test_every_json_response_crosses_the_privacy_filter(tmp_path) -> None:
    app = create_app(settings_for(tmp_path / "privacy.db"))
    profile_dir = Path.home() / ".linkedin-mcp/profile"

    @app.get("/api/test/privacy")
    def leak_fixture() -> dict[str, object]:
        return {
            "section_errors": {
                "experience": {
                    "error_type": "test",
                    "runtime": {
                        "source_profile_dir": str(profile_dir),
                        "portable_cookie_path": str(
                            Path.home() / ".linkedin-mcp/cookies.json"
                        ),
                        "hostname": "private-host",
                    },
                    "error_message": str(Path.home() / ".linkedin-mcp/profile failed"),
                    "hostname": "internal-host",
                    "issue_template_path": "/opt/dashboard/issues/template.md",
                    "runtime_storage_state_path": (
                        r"C:\Users\operator\linkedin\state.json"
                    ),
                    "trace_output_dir": "/srv/dashboard/private-traces",
                    "local_cache_path": "/var/private/dashboard-cache",
                    "suggested_gist_command": "upload-internal-diagnostics",
                },
                "profile_url": "https://www.linkedin.com/in/safe-person/",
                "relative_url": "/in/safe-person/",
                "custom_error": (
                    "failed at /srv/custom-dashboard/session.db and "
                    r"C:\private-dashboard\cookies.json plus "
                    "file:///custom/private/runtime.json"
                ),
            },
            "mcp_url": "http://127.0.0.1:8000/mcp",
        }

    with client_for(app) as client:
        response = client.get("/api/test/privacy")

    body = response.text
    payload = response.json()
    assert response.status_code == 200
    assert "runtime" not in payload["section_errors"]["experience"]
    assert "mcp_url" not in payload
    assert "hostname" not in body
    assert "issue_template_path" not in body
    assert "runtime_storage_state_path" not in body
    assert "trace_output_dir" not in body
    assert "local_cache_path" not in body
    assert "suggested_gist_command" not in body
    assert "/srv/custom-dashboard/session.db" not in body
    assert r"C:\private-dashboard\cookies.json" not in body
    assert "file:///custom/private/runtime.json" not in body
    assert "https://www.linkedin.com/in/safe-person/" in body
    assert '"relative_url":"/in/safe-person/"' in body
    assert str(Path.home()) not in body
    assert ".linkedin-mcp" not in body


def test_non_json_streams_are_not_buffered_or_modified(tmp_path) -> None:
    app = create_app(settings_for(tmp_path / "stream.db"))

    def chunks() -> Iterator[str]:
        yield "event: ready\n"
        yield "data: ok\n\n"

    @app.get("/api/test/events")
    def events() -> StreamingResponse:
        return StreamingResponse(chunks(), media_type="text/event-stream")

    with client_for(app) as client:
        response = client.get("/api/test/events")

    assert response.status_code == 200
    assert response.text == "event: ready\ndata: ok\n\n"


def test_sse_events_are_sanitized_across_chunk_boundaries(tmp_path) -> None:
    app = create_app(settings_for(tmp_path / "private-stream.db"))
    home = str(Path.home())

    def chunks() -> Iterator[str]:
        yield "event: ready\ndata: safe\n\n"
        yield f'event: profile\ndata: {{"runtime":{{"cookie_path":"{home}/.link'
        yield 'edin-mcp/profile"},"error":"http://operator:secret@127.0.0.1:'
        yield '8000/mcp"}\n\n'
        yield 'event: malformed\ndata: {"runtime":{"hostname":"private-host"}\n\n'

    @app.get("/api/test/private-events")
    def events() -> StreamingResponse:
        return StreamingResponse(chunks(), media_type="text/event-stream")

    with client_for(app) as client:
        response = client.get("/api/test/private-events")

    assert response.status_code == 200
    assert response.text.startswith("event: ready\ndata: safe\n\n")
    assert "runtime" not in response.text
    assert home not in response.text
    assert ".linkedin-mcp" not in response.text
    assert "operator:secret" not in response.text
    assert "private-host" not in response.text
    assert "http://[redacted]@127.0.0.1:8000/mcp" in response.text


def test_sse_incremental_parser_handles_bom_and_mixed_line_endings() -> None:
    stream = (
        b'\xef\xbb\xbfevent: profile\rdata: {"hostname":"private-host",'
        b'"suggested_gist_command":"upload","url":"//first:secret@alias@host/x"}'
        b"\n\r"
        b"event: malformed\r\ndata: \xef\xbb\xbf"
        b'{"runtime":{"cookie_path":"secret"}\r\n\n'
        b"event: final\ndata: safe"
    )
    parser = _SSEParser()
    output: list[bytes] = []

    for index, byte in enumerate(stream):
        output.append(parser.feed(bytes([byte]), final=index == len(stream) - 1))

    text = b"".join(output).decode("utf-8")
    assert text.startswith('event: profile\ndata: {"url":"//[redacted]@host/x"}\n\n')
    assert 'event: malformed\ndata: {"detail":' in text
    assert text.endswith("event: final\ndata: safe")
    assert "runtime" not in text
    assert "private-host" not in text
    assert "suggested_gist_command" not in text
    assert "first:secret@alias" not in text


def test_sse_parser_strips_exactly_one_stream_leading_bom() -> None:
    parser = _SSEParser()

    assert parser.feed(b"\xef", final=False) == b""
    assert parser.feed(b"\xbb", final=False) == b""
    assert parser.feed(b"\xbfdata: ok\n\n", final=True) == b"data: ok\n\n"

    second = _SSEParser().feed(b"\xef\xbb\xbf\xef\xbb\xbf: keep-second\n\n", final=True)
    assert second.startswith(b"\xef\xbb\xbf")


def test_sse_parser_supports_every_mixed_event_boundary_byte_by_byte() -> None:
    line_endings = (b"\r", b"\n", b"\r\n")
    for content_ending, blank_ending in product(line_endings, repeat=2):
        if (content_ending, blank_ending) == (b"\r", b"\n"):
            # Adjacent CR + LF is one CRLF ending, not two event-boundary lines.
            continue
        stream = b"data: one" + content_ending + blank_ending
        parser = _SSEParser()
        output: list[bytes] = []
        for index, byte in enumerate(stream):
            output.append(parser.feed(bytes([byte]), final=index == len(stream) - 1))
        assert b"".join(output) == b"data: one\n\n"


def test_sse_parser_does_not_dispatch_before_blank_line() -> None:
    parser = _SSEParser()

    assert parser.feed(b"data: delayed\r", final=False) == b""
    assert parser.feed(b"\n", final=False) == b""
    assert parser.feed(b"\r", final=False) == b""
    assert parser.feed(b"\n", final=False) == b"data: delayed\n\n"


@pytest.mark.parametrize(
    "payload",
    [
        b'{"value":"\\ud800leading"}',
        b'{"value":"trailing\\udfff"}',
    ],
)
def test_sse_surrogate_escape_becomes_safe_error_byte_by_byte(payload: bytes) -> None:
    stream = (
        b"event: unsafe\ndata: "
        + payload
        + b"\n\nevent: after\ndata: still-streaming\n\n"
    )
    parser = _SSEParser()
    output: list[bytes] = []

    for index, byte in enumerate(stream):
        output.append(parser.feed(bytes([byte]), final=index == len(stream) - 1))

    assert b"".join(output) == (
        b'event: unsafe\ndata: {"detail":"Response could not be safely serialized"}\n\n'
        b"event: after\ndata: still-streaming\n\n"
    )


def test_sse_redacts_custom_diagnostic_paths_byte_by_byte() -> None:
    stream = (
        b"event: diagnostic\r\n"
        b'data: {"issue_template_path":"/custom/issues/template.md",'
        b'"runtime_storage_state_path":"C:\\\\Users\\\\operator\\\\state.json",'
        b'"trace_dir":"/srv/private-traces",'
        b'"message":"failed /opt/private/session.db",'
        b'"profile":"/in/safe-person/"}\r\n\r\n'
    )
    parser = _SSEParser()
    output: list[bytes] = []
    for index, byte in enumerate(stream):
        output.append(parser.feed(bytes([byte]), final=index == len(stream) - 1))

    text = b"".join(output).decode()
    assert "issue_template_path" not in text
    assert "runtime_storage_state_path" not in text
    assert "trace_dir" not in text
    assert "/opt/private/session.db" not in text
    assert '"message":"failed [redacted-path]"' in text
    assert '"profile":"/in/safe-person/"' in text


def test_structured_json_suffix_crosses_privacy_filter(tmp_path) -> None:
    app = create_app(settings_for(tmp_path / "problem-json.db"))

    @app.get("/api/test/problem-json")
    def problem_json() -> JSONResponse:
        return JSONResponse(
            {"runtime": {"cookie_path": str(Path.home() / ".linkedin-mcp")}},
            media_type="application/problem+json",
        )

    with client_for(app) as client:
        response = client.get("/api/test/problem-json")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {}


def test_malformed_declared_json_fails_closed(tmp_path) -> None:
    app = create_app(settings_for(tmp_path / "malformed-json.db"))

    @app.get("/api/test/malformed-json")
    def malformed_json() -> Response:
        return Response(
            content=b'{"secret":"/Users/private/.linkedin-mcp/profile"',
            media_type="application/json",
        )

    with client_for(app) as client:
        response = client.get("/api/test/malformed-json")

    assert response.status_code == 500
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"detail": "Response could not be safely serialized"}
    assert "secret" not in response.text
    assert ".linkedin-mcp" not in response.text


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_declared_json_fails_closed(tmp_path, constant: str) -> None:
    app = create_app(settings_for(tmp_path / f"non-finite-{constant}.db"))

    @app.get("/api/test/non-finite")
    def non_finite_json() -> Response:
        return Response(
            content=f'{{"value":{constant}}}'.encode(),
            media_type="application/json",
        )

    with client_for(app) as client:
        response = client.get("/api/test/non-finite")

    assert response.status_code == 500
    assert response.json() == {"detail": "Response could not be safely serialized"}


@pytest.mark.parametrize(
    "payload",
    [
        b'{"value":"\\ud800leading"}',
        b'{"value":"trailing\\udfff"}',
    ],
)
def test_surrogate_json_escape_fails_closed(tmp_path, payload: bytes) -> None:
    app = create_app(settings_for(tmp_path / "surrogate.db"))

    @app.get("/api/test/surrogate")
    def surrogate_json() -> Response:
        return Response(content=payload, media_type="application/json")

    with client_for(app) as client:
        response = client.get("/api/test/surrogate")

    assert response.status_code == 500
    assert response.json() == {"detail": "Response could not be safely serialized"}


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_values_cannot_be_encoded(value: float) -> None:
    with pytest.raises(ValueError, match="Out of range float values"):
        _strict_json_dumps({"value": value})


def test_surrogate_values_cannot_be_encoded() -> None:
    with pytest.raises(UnicodeEncodeError):
        _strict_json_dumps({"value": "\ud800unsafe"})


def test_credential_urls_are_redacted_in_values_and_keys_without_corruption() -> None:
    sanitized = sanitize_for_frontend(
        {
            "//first:secret@alias@host.example/path": (
                "https://first:secret@alias@host.example/path"
            ),
            "safe": [
                "https://host.example/path@segment",
                "https://host.example/path//name@segment",
                "mailto:user@example.com",
            ],
        }
    )

    assert sanitized == {
        "//[redacted]@host.example/path": ("https://[redacted]@host.example/path"),
        "safe": [
            "https://host.example/path@segment",
            "https://host.example/path//name@segment",
            "mailto:user@example.com",
        ],
    }


def test_audit_api_redacts_credentials_embedded_in_strings(tmp_path) -> None:
    app = create_app(settings_for(tmp_path / "audit-privacy.db"))
    with client_for(app) as client:
        assert client.get("/api/health").status_code == 200
        with app.state.database.sessions.begin() as db_session:
            db_session.add(
                DashboardSession(
                    id="session-api-privacy",
                    created_at="2026-09-02T12:00:00+00:00",
                    label="Privacy",
                    purge_after="2026-09-03T12:00:00+00:00",
                    nav_budget=120,
                    nav_used=0,
                    send_enabled=False,
                )
            )
        append_audit_event(
            app.state.database,
            session_id="session-api-privacy",
            actor="system",
            action="mcp.failed",
            subject_type="mcp",
            subject_id="local",
            detail={
                "error_message": "request to "
                "http://operator:secret@127.0.0.1:8000/mcp failed",
                "http://key-user:key-secret@127.0.0.1/private": "key test",
            },
        )
        response = client.get("/api/audit")

    assert response.status_code == 200
    body = response.text
    assert "operator:secret" not in body
    assert "key-user:key-secret" not in body
    assert "http://[redacted]@127.0.0.1:8000/mcp" in body


def test_unsafe_method_origin_guard(tmp_path) -> None:
    app = create_app(settings_for(tmp_path / "origin.db"))

    @app.post("/api/test/mutate")
    def mutate() -> dict[str, bool]:
        return {"accepted": True}

    with client_for(app) as client:
        no_origin = client.post("/api/test/mutate")
        configured_origin = client.post(
            "/api/test/mutate", headers={"Origin": "http://127.0.0.1:5173"}
        )
        foreign_origin = client.post(
            "/api/test/mutate", headers={"Origin": "http://evil.example"}
        )

    assert no_origin.status_code == 200
    assert configured_origin.status_code == 200
    assert foreign_origin.status_code == 403
    assert foreign_origin.json() == {"detail": "Origin is not allowed"}


def test_ipv6_frontend_origin_guard_matches_configured_vite_origin(tmp_path) -> None:
    settings = Settings(
        frontend_host="::1",
        frontend_port=5191,
        db_path=tmp_path / "ipv6-origin.db",
    )
    app = create_app(settings)

    @app.post("/api/test/ipv6-mutate")
    def mutate() -> dict[str, bool]:
        return {"accepted": True}

    with client_for(app) as client:
        configured = client.post(
            "/api/test/ipv6-mutate",
            headers={"Origin": "http://[::1]:5191"},
        )
        wrong_port = client.post(
            "/api/test/ipv6-mutate",
            headers={"Origin": "http://[::1]:5173"},
        )

    assert configured.status_code == 200
    assert wrong_port.status_code == 403


@pytest.mark.parametrize(
    ("host", "host_header"),
    [("127.0.0.2", "127.0.0.2:8787"), ("[::1]", "[::1]:8787")],
)
def test_configured_loopback_host_is_accepted(
    host: str, host_header: str, tmp_path
) -> None:
    settings = Settings(host=host, db_path=tmp_path / "host.db")
    app = create_app(settings)

    with client_for(app) as client:
        response = client.get("/api/health", headers={"Host": host_header})

    assert response.status_code == 200


def test_unconfigured_loopback_host_is_rejected(tmp_path) -> None:
    app = create_app(settings_for(tmp_path / "host-rejected.db"))

    with client_for(app, base_url="http://127.0.0.2") as client:
        response = client.get("/api/health")

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid host header"}
