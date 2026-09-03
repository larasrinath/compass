from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import Response
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.testclient import TestClient
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
                }
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
