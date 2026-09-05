"""Operational settings checks use local fixtures; no browser or MCP requests."""

from fastapi.testclient import TestClient
from linkedin_dashboard.configuration import Configuration
from linkedin_dashboard.db.models import (
    AppConfiguration,
    SearchDownload,
    SearchPagination,
)
from linkedin_dashboard.main import create_app
from linkedin_dashboard.settings import Settings


class NoNetworkExecutor:
    profile_concurrency = 2

    async def execute(self, *args, **kwargs):
        raise AssertionError("Settings must not make external calls")


def test_configuration_persists_and_rejects_invalid_values(tmp_path):
    settings = Settings(db_path=tmp_path / "settings.db", inter_call_delay_seconds=4)
    app = create_app(settings, queue_executor=NoNetworkExecutor())
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.get("/api/settings")
        assert response.status_code == 200
        original = response.json()
        assert original["inter_call_delay_seconds"] == 4
        changed = {**original, "profile_concurrency": 1, "download_batch_limit": 50}
        assert client.put("/api/settings", json=changed).status_code == 200
        for invalid in (
            {"profile_concurrency": 3},
            {"download_batch_limit": 1001},
            {"automatic_downloads": "false"},
            {"mcp_url": "http://external.test"},
        ):
            assert (
                client.put("/api/settings", json={**changed, **invalid}).status_code
                == 422
            )
        assert client.get("/api/settings").json() == changed
    with TestClient(
        create_app(settings, queue_executor=NoNetworkExecutor()),
        base_url="http://127.0.0.1",
    ) as client:
        assert client.get("/api/settings").json() == changed


def test_new_search_snapshots_batch_limits_and_automation(tmp_path):
    app = create_app(
        Settings(db_path=tmp_path / "search.db"), queue_executor=NoNetworkExecutor()
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        client.get("/api/settings")

        from linkedin_dashboard.db.models import QueueControl

        with app.state.database.sessions.begin() as db:
            control = db.get(QueueControl, 1)
            assert control is not None
            control.state = "paused"
            control.pause_reason = "AUTH_REQUIRED"
            control.operator_resume_required = True
        sid = client.post("/api/session", json={"label": "Settings fixture"}).json()[
            "id"
        ]
        brief = client.post(
            "/api/briefs",
            json={
                "session_id": sid,
                "job_description": "Engineer",
                "required_skills": [{"term": "Python"}],
            },
        ).json()
        config = Configuration(download_batch_limit=75, search_page_limit=8)
        client.put("/api/settings", json=config.model_dump())
        response = client.post(
            "/api/searches",
            json={"session_id": sid, "brief_id": brief["id"], "keywords": "Python"},
        )
        assert response.status_code == 202, response.text
        run_id = response.json()["search_run_id"]
        client.put(
            "/api/settings", json={**config.model_dump(), "download_batch_limit": 25}
        )
        with app.state.database.sessions() as db:
            batch = db.get(SearchDownload, run_id)
            pages = db.get(SearchPagination, run_id)
            assert batch is not None and batch.profile_limit == 75
            assert pages is not None and pages.page_limit == 8
            assert db.get(AppConfiguration, 1) is not None

        # Explicit booleans override saved automatic defaults for API clients.
        discovery_only = client.post(
            "/api/searches",
            json={
                "session_id": sid,
                "brief_id": brief["id"],
                "keywords": "Python backend",
                "automatic_downloads": False,
                "paginate": False,
            },
        )
        assert discovery_only.status_code == 202, discovery_only.text
        discovery_id = discovery_only.json()["search_run_id"]
        with app.state.database.sessions() as db:
            assert db.get(SearchDownload, discovery_id) is None
            assert db.get(SearchPagination, discovery_id) is None
