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
                "reference": {"url": "/jobs/view/123456/"},
                "unsafe_relative_url": "/in/safe-person/cookies.json",
                "diagnostic": ["/in/safe-person/", "/jobs/view/123456/"],
                "custom_error": (
                    "failed at /srv/custom-dashboard/session.db and "
                    r"C:\private-dashboard\cookies.json plus "
                    "file:///custom/private/runtime.json and "
                    "label:/opt/private/trace.log and "
                    "/jobs/runtime/private.db"
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
    assert '"reference":{"url":"/jobs/view/123456/"}' in body
    assert '"unsafe_relative_url":"[redacted-path]"' in body
    assert '"diagnostic":["[redacted-path]","[redacted-path]"]' in body
    assert "label:/opt/private/trace.log" not in body
    assert "/jobs/runtime/private.db" not in body
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
    assert text.startswith('event: profile\ndata: {"url":"[redacted-path]"}\n\n')
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
        b'"message":"failed /opt/private/session.db; label:/opt/private/log; '
        b'/jobs/runtime/cache.db; C:\\\\private\\\\cookies.json",'
        b'"relative_url":"/in/safe-person/",'
        b'"unsafe_relative_url":"/in/safe-person/cookies.json"}\r\n\r\n'
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
    assert '"message":"failed [redacted-path]' in text
    assert "/jobs/runtime/cache.db" not in text
    assert "/in/safe-person/cookies.json" not in text
    assert '"relative_url":"/in/safe-person/"' in text


def test_json_response_headers_cross_the_privacy_boundary(tmp_path) -> None:
    app = create_app(settings_for(tmp_path / "json-header-privacy.db"))

    @app.get("/api/test/json-headers")
    def json_headers() -> JSONResponse:
        return JSONResponse(
            {"status": "ok"},
            headers={
                "Cache-Control": "no-store",
                "Set-Cookie": "session=secret",
                "X-Api-Key": "plain-secret-value",
                "X-Runtime-Path": "/opt/private/runtime.json",
                "X-Diagnostic": (
                    "label:/opt/private/trace.log; "
                    "http://operator:secret@127.0.0.1:8000/mcp"
                ),
            },
        )

    with client_for(app) as client:
        response = client.get(
            "/api/test/json-headers",
            headers={"X-Correlation-ID": "header-test"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.headers["content-length"] == str(len(response.content))
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-correlation-id"] == "header-test"
    assert "set-cookie" not in response.headers
    assert "x-api-key" not in response.headers
    assert "x-runtime-path" not in response.headers
    assert response.headers["x-diagnostic"] == (
        "label:[redacted-path] http://[redacted]@127.0.0.1:8000/mcp"
    )


def test_sse_response_headers_cross_the_privacy_boundary(tmp_path) -> None:
    app = create_app(settings_for(tmp_path / "sse-header-privacy.db"))

    @app.get("/api/test/sse-headers")
    def sse_headers() -> StreamingResponse:
        return StreamingResponse(
            iter([b"data: ok\n\n"]),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "X-Cookie-Path": "/opt/private/cookies.json",
                "X-Diagnostic": r"C:\private\stream.log",
            },
        )

    with client_for(app) as client:
        response = client.get("/api/test/sse-headers")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.headers["x-correlation-id"]
    assert "content-length" not in response.headers
    assert "x-cookie-path" not in response.headers
    assert response.headers["x-diagnostic"] == "[redacted-path]"


def test_passthrough_response_headers_cross_the_privacy_boundary(tmp_path) -> None:
    app = create_app(settings_for(tmp_path / "text-header-privacy.db"))

    @app.get("/api/test/text-headers")
    def text_headers() -> Response:
        return Response(
            "safe",
            media_type="text/plain",
            headers={
                "ETag": '"stable"',
                "Authorization": "Bearer secret",
                "X-Diagnostic": "/jobs/runtime/private.db",
            },
        )

    with client_for(app) as client:
        response = client.get("/api/test/text-headers")

    assert response.status_code == 200
    assert response.text == "safe"
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["content-length"] == "4"
    assert response.headers["etag"] == '"stable"'
    assert response.headers["x-correlation-id"]
    assert "authorization" not in response.headers
    assert response.headers["x-diagnostic"] == "[redacted-path]"


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
        "[redacted-path]": "https://[redacted]@host.example/path",
        "safe": [
            "https://host.example/path@segment",
            "https://host.example/path//name@segment",
            "mailto:user@example.com",
        ],
    }


def test_linkedin_urls_sanitize_query_fragment_and_matrix_credentials() -> None:
    sanitized = sanitize_for_frontend(
        {
            "url": (
                "https://www.linkedin.com/in/safe-person;token=matrix-secret/"
                "?safe=visible&access_token=query-secret"
                "#section?password=fragment-secret&tab=experience"
            ),
            "relative_url": (
                "/in/safe-person;authToken=relative-matrix/"
                "?safe=yes#token=relative-fragment"
            ),
            "profile_url": "//fileserver/private/profile",
        }
    )

    serialized = _strict_json_dumps(sanitized)
    for secret in (
        "matrix-secret",
        "query-secret",
        "fragment-secret",
        "relative-matrix",
        "relative-fragment",
        "fileserver/private/profile",
    ):
        assert secret not in serialized
    assert "safe=visible" in serialized
    assert "tab=experience" in serialized
    assert "safe=yes" in serialized
    assert sanitized["profile_url"] == "[redacted-path]"


@pytest.mark.parametrize("separator", [":", "="])
def test_credential_labels_accept_colon_and_equals(separator: str) -> None:
    sanitized = sanitize_for_frontend(
        {
            "message": (
                f"proxy_password{separator} proxy-secret "
                f"li_at{separator} cookie-secret "
                f"Authorization{separator} Bearer bearer-secret"
            )
        }
    )

    serialized = _strict_json_dumps(sanitized)
    assert "proxy-secret" not in serialized
    assert "cookie-secret" not in serialized
    assert "bearer-secret" not in serialized


@pytest.mark.parametrize(
    "value",
    [
        "file://authority-only",
        "file://user:secret@authority/private",
        "file:relative-private",
        "//server/share/private",
        "//127.0.0.1/C$/private",
    ],
)
def test_all_file_and_protocol_relative_filesystem_urls_are_redacted(
    value: str,
) -> None:
    assert sanitize_for_frontend({"url": value}) == {"url": "[redacted-path]"}


def test_response_headers_sanitize_url_components_and_colon_labels(tmp_path) -> None:
    app = create_app(settings_for(tmp_path / "header-components.db"))

    @app.get("/api/test/header-components")
    def header_components() -> JSONResponse:
        return JSONResponse(
            {"ok": True},
            headers={
                "X-Diagnostic": (
                    "https://www.linkedin.com/in/safe;token=matrix-secret"
                    "?safe=yes#password=fragment-secret "
                    "proxy_password: label-secret file://authority-only"
                )
            },
        )

    with client_for(app) as client:
        response = client.get("/api/test/header-components")

    header = response.headers["x-diagnostic"]
    assert "matrix-secret" not in header
    assert "fragment-secret" not in header
    assert "label-secret" not in header
    assert "file://" not in header
    assert "authority-only" not in header
    assert "safe=yes" in header


def test_json_response_redacts_query_labeled_file_and_header_secrets(
    tmp_path,
) -> None:
    app = create_app(settings_for(tmp_path / "expanded-privacy.db"))

    @app.get("/api/test/expanded-privacy")
    def expanded_privacy() -> JSONResponse:
        return JSONResponse(
            {
                "url": (
                    "https://example.test/profile?access_token=access-query-secret"
                    "&token=token-query-secret&key=key-query-secret"
                    "&password=password-query-secret&cookie=cookie-query-secret"
                    "&authToken=auth-query-secret&safe=visible"
                ),
                "message": (
                    "proxy_password=proxy-secret "
                    "Authorization: Bearer bearer-secret "
                    "Cookie: li_at=cookie-secret; JSESSIONID=visible"
                ),
                "files": [
                    "file:///opt/private/one",
                    "file://localhost/opt/private/two",
                    "file://fileserver/private/three",
                    "file:/opt/private/four",
                ],
                "network_share": "//fileserver/private/share",
                "safe_url_context": {"url": "//www.linkedin.com/in/safe-person/"},
            },
            headers={
                "X-AccessToken": "header-access-secret",
                "X-ApiKey": "header-key-secret",
                "X-Diagnostic": (
                    "https://example.test/?password=header-query-secret "
                    "proxy_password=header-label-secret"
                ),
            },
        )

    with client_for(app) as client:
        response = client.get("/api/test/expanded-privacy")

    body = response.text
    assert response.status_code == 200
    for secret in (
        "access-query-secret",
        "token-query-secret",
        "key-query-secret",
        "password-query-secret",
        "cookie-query-secret",
        "auth-query-secret",
        "proxy-secret",
        "bearer-secret",
        "cookie-secret",
        "file://",
        "fileserver/private/share",
    ):
        assert secret not in body
    assert "safe=visible" in body
    assert "JSESSIONID=visible" in body
    assert '"network_share":"[redacted-path]"' in body
    assert '"url":"//www.linkedin.com/in/safe-person/"' in body
    assert "x-accesstoken" not in response.headers
    assert "x-apikey" not in response.headers
    assert "header-query-secret" not in response.headers["x-diagnostic"]
    assert "header-label-secret" not in response.headers["x-diagnostic"]


def test_byte_split_sse_redacts_query_labeled_and_file_secrets(tmp_path) -> None:
    app = create_app(settings_for(tmp_path / "expanded-sse-privacy.db"))
    payload = (
        b'data: {"url":"https://www.linkedin.com/in/safe;token=matrix-secret/'
        b'?safe=yes#password=fragment-secret",'
        b'"message":"proxy_password: proxy-secret Authorization=Bearer '
        b'bearer-secret Cookie: li_at=cookie-secret",'
        b'"file":"file://localhost/opt/private/data",'
        b'"profile_url":"//fileserver/private/share"}\n\n'
    )

    @app.get("/api/test/expanded-sse-privacy")
    def expanded_sse_privacy() -> StreamingResponse:
        return StreamingResponse(
            (bytes([byte]) for byte in payload),
            media_type="text/event-stream",
            headers={
                "X-AccessToken": "header-secret",
                "X-Diagnostic": "Cookie: li_at=header-cookie-secret",
            },
        )

    with client_for(app) as client:
        response = client.get("/api/test/expanded-sse-privacy")

    for secret in (
        "matrix-secret",
        "fragment-secret",
        "proxy-secret",
        "bearer-secret",
        "cookie-secret",
        "file://",
        "fileserver/private/share",
    ):
        assert secret not in response.text
    assert "x-accesstoken" not in response.headers
    assert "header-cookie-secret" not in response.headers["x-diagnostic"]
    assert "safe=yes" in response.text


def test_percent_decoded_parameter_values_and_diagnostic_keys_are_private() -> None:
    sanitized = sanitize_for_frontend(
        {
            "url": (
                "https://www.linkedin.com/in/safe-person;"
                "detail=%252FUsers%252Foperator%252F.linkedin-mcp%252Fprofile/"
                "?safe=visible&note=credential%253Ddeep-secret"
                "#tab=experience&debug=proxy_username%3Doperator-secret"
            ),
            "relative_url": (
                "/in/safe-person/?next=cookie_path%3D%252Ftmp%252Fcookies.json"
            ),
            "credential": "json-credential-secret",
            "proxy_username": "json-proxy-user",
            "x_api_key": "json-api-secret",
            "safe": "ordinary value",
        }
    )

    serialized = _strict_json_dumps(sanitized)
    for secret in (
        "deep-secret",
        "operator-secret",
        "json-credential-secret",
        "json-proxy-user",
        "json-api-secret",
        "Users%252Foperator",
        "cookies.json",
    ):
        assert secret not in serialized
    assert "safe=visible" in serialized
    assert "tab=experience" in serialized
    assert sanitized["safe"] == "ordinary value"


def test_decoded_url_privacy_covers_sse_headers_and_benign_header_names(
    tmp_path,
) -> None:
    app = create_app(settings_for(tmp_path / "decoded-privacy.db"))
    payload = (
        b'data: {"url":"/in/safe-person/?note=access_token%253Dsse-secret",'
        b'"profile_url":"https://www.linkedin.com/in/safe;'
        b'debug=%252Fhome%252Foperator%252F.linkedin-mcp%252Fprofile/"}\n\n'
    )

    @app.get("/api/test/decoded-privacy")
    def decoded_privacy() -> StreamingResponse:
        return StreamingResponse(
            (bytes([byte]) for byte in payload),
            media_type="text/event-stream",
            headers={
                "X-Diagnostic": (
                    "https://www.linkedin.com/in/safe/?note=x-api-key%253Dheader-secret"
                ),
                "X-Monkey": "benign-monkey",
                "X-Keynote": "benign-keynote",
            },
        )

    with client_for(app) as client:
        response = client.get("/api/test/decoded-privacy")

    assert "sse-secret" not in response.text
    assert "linkedin-mcp" not in response.text
    assert "header-secret" not in response.headers["x-diagnostic"]
    assert response.headers["x-monkey"] == "benign-monkey"
    assert response.headers["x-keynote"] == "benign-keynote"


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


@pytest.mark.parametrize("frontend_host", ["127.0.0.1", "::1"])
def test_default_http_port_origin_guard_uses_canonical_origin(
    tmp_path, frontend_host: str
) -> None:
    settings = Settings(
        frontend_host=frontend_host,
        frontend_port=80,
        db_path=tmp_path / "default-port-origin.db",
    )
    app = create_app(settings)

    @app.post("/api/test/default-port-mutate")
    def mutate() -> dict[str, bool]:
        return {"accepted": True}

    canonical_origin = "http://[::1]" if frontend_host == "::1" else "http://127.0.0.1"
    explicit_port = f"{canonical_origin}:80"
    with client_for(app) as client:
        configured = client.post(
            "/api/test/default-port-mutate",
            headers={"Origin": canonical_origin},
        )
        noncanonical = client.post(
            "/api/test/default-port-mutate",
            headers={"Origin": explicit_port},
        )

    assert configured.status_code == 200
    assert noncanonical.status_code == 403


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
