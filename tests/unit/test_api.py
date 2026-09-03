from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from linkedin_dashboard.main import create_app
from linkedin_dashboard.settings import Settings


def settings_for(path: Path) -> Settings:
    return Settings(db_path=path, llm_provider="null", send_enabled=False)


def test_health_smoke_and_correlation_id(tmp_path) -> None:
    app = create_app(settings_for(tmp_path / "health.db"))
    with TestClient(app) as client:
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

    with TestClient(app) as client:
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

    with TestClient(app) as client:
        response = client.get("/api/test/events")

    assert response.status_code == 200
    assert response.text == "event: ready\ndata: ok\n\n"
