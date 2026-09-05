from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from linkedin_dashboard.db.models import Candidate, Job, QueueControl, SearchPagination
from linkedin_dashboard.main import create_app
from linkedin_dashboard.queue.jobs import SearchPeoplePayload
from sqlalchemy import select
from test_automatic_downloads import SearchAndProfileExecutor, setup
from test_discovery import settings


class PagedExecutor(SearchAndProfileExecutor):
    def __init__(self, pages):
        super().__init__()
        self.pages = pages

    async def execute(self, payload, capture_raw, report_progress):
        if isinstance(payload, SearchPeoplePayload):
            self.people = self.pages.get(payload.page, [])
        return await super().execute(payload, capture_raw, report_progress)


def completed(app, root):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        with app.state.database.sessions() as db:
            group = db.get(SearchPagination, root)
            active = db.scalar(
                select(Job.id).where(Job.state.in_(["queued", "running", "pending"]))
            )
            if group.stop_reason and active is None:
                return group.stop_reason
        time.sleep(0.02)
    raise AssertionError("pagination did not finish")


@pytest.mark.parametrize("network", [["F"], ["S"], ["O"], None])
def test_pages_keep_filters_dedupe_downloads_and_one_saved_search(tmp_path, network):
    executor = PagedExecutor({1: ["ada", "grace"], 2: ["grace", "linus"]})
    app = create_app(settings(tmp_path / "pages.db"), queue_executor=executor)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        payload = {
            **setup(client),
            "paginate": True,
            "automatic_downloads": True,
            "network": network,
            "location": "Chicago",
            "current_company": "123",
        }
        response = client.post("/api/searches", json=payload)
        assert response.status_code == 202, response.text
        root = response.json()["search_run_id"]
        assert completed(app, root) == "exhausted"
        calls = [p for p in executor.calls if isinstance(p, SearchPeoplePayload)]
        assert [p.page for p in calls] == [1, 2, 3]
        assert all(
            p.network == network
            and p.current_company == "123"
            and p.location == "Chicago"
            and p.keywords == "Python"
            for p in calls
        )
        profiles = [
            p.linkedin_username
            for p in executor.calls
            if not isinstance(p, SearchPeoplePayload)
        ]
        assert sorted(profiles) == ["ada", "grace", "linus"]
        runs = client.get(
            "/api/searches", params={"session_id": payload["session_id"]}
        ).json()
        assert len(runs) == 1
        assert runs[0]["pagination"]["pages_completed"] == 3
        assert runs[0]["person_reference_count"] == 3
        assert len(runs[0]["pages"]) == 3
        pool = client.get(
            "/api/candidate-pool", params={"session_id": payload["session_id"]}
        ).json()
        assert len(pool) == 3
        assert all(p["stage"] == "stage1" and p["source_count"] == 1 for p in pool)
        assert {s["search_run_id"] for p in pool for s in p["sources"]} == {root}
        assert {s["page_number"] for p in pool for s in p["sources"]} == {1, 2}
        for page in runs[0]["pages"]:
            assert (
                client.get("/api/searches/" + page["run_id"]).json()["raw_text"]
                is not None
            )


def test_repeated_page_stops_without_loop(tmp_path):
    executor = PagedExecutor({1: ["ada"], 2: ["ada"], 3: ["unexpected"]})
    app = create_app(settings(tmp_path / "repeat.db"), queue_executor=executor)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        root = client.post(
            "/api/searches", json={**setup(client), "paginate": True}
        ).json()["search_run_id"]
        assert completed(app, root) == "repeated_page"
        assert [p.page for p in executor.calls] == [1, 2]


def test_stop_before_first_page_prevents_all_navigation(tmp_path):
    executor = PagedExecutor({1: ["ada"]})
    app = create_app(settings(tmp_path / "stop.db"), queue_executor=executor)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        payload = setup(client)
        with app.state.database.sessions.begin() as db:
            control = db.get(QueueControl, 1)
            control.state = "paused"
            control.pause_reason = "operator"
            control.operator_resume_required = True
        root = client.post("/api/searches", json={**payload, "paginate": True}).json()[
            "search_run_id"
        ]
        assert client.post("/api/searches/" + root + "/stop").status_code == 202
        client.post("/api/queue/resume")
        assert completed(app, root) == "stopped"
        assert executor.calls == []


def test_discovery_passes_first_page_and_honors_1000_cap(tmp_path):
    executor = PagedExecutor(
        {
            1: [f"person-{i}" for i in range(15)],
            2: [f"person-{i}" for i in range(15, 25)],
        }
    )
    app = create_app(settings(tmp_path / "cap.db"), queue_executor=executor)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        payload = setup(client)
        with app.state.database.sessions.begin() as db:
            db.add_all(
                [
                    Candidate(
                        id=f"old-{i}",
                        session_id=payload["session_id"],
                        username=f"old-{i}",
                        profile_url=f"https://www.linkedin.com/in/old-{i}",
                        first_seen_at="2026-09-05T00:00:00+00:00",
                        stage="discovered",
                        retrieval_status="pending",
                    )
                    for i in range(980)
                ]
            )
        root = client.post("/api/searches", json={**payload, "paginate": True}).json()[
            "search_run_id"
        ]
        assert completed(app, root) == "profile_limit"
        assert [p.page for p in executor.calls] == [1, 2]
        with app.state.database.sessions() as db:
            assert len(list(db.scalars(select(Candidate.id)))) == 1000


def test_restart_continues_next_page_without_replaying_first(tmp_path):
    config = settings(tmp_path / "restart.db")
    first = PagedExecutor({1: ["ada"]})
    app = create_app(config, queue_executor=first)

    async def defer():
        pass

    app.state.pagination_service.dispatch_pending = defer
    from test_discovery import wait_for_job

    with TestClient(app, base_url="http://127.0.0.1") as client:
        payload = setup(client)
        run = client.post("/api/searches", json={**payload, "paginate": True}).json()
        wait_for_job(app, run["job_id"])
    second = PagedExecutor({2: ["grace"]})
    recovered = create_app(config, queue_executor=second)
    with TestClient(recovered, base_url="http://127.0.0.1") as client:
        assert client.get("/api/queue/status").status_code == 200
        assert completed(recovered, run["search_run_id"]) == "exhausted"
        assert [p.page for p in second.calls] == [2, 3]
        assert (
            client.post(
                "/api/searches/" + run["search_run_id"] + "/downloads"
            ).status_code
            == 202
        )
        from test_automatic_downloads import settled

        settled(recovered, run["search_run_id"], 2)
        assert sorted(
            p.linkedin_username
            for p in second.calls
            if not isinstance(p, SearchPeoplePayload)
        ) == ["ada", "grace"]


@pytest.mark.parametrize("failure", ["failed", "rate_limited"])
def test_failed_later_page_keeps_earlier_people_and_stops(tmp_path, failure):
    class FailingPage(PagedExecutor):
        async def execute(self, payload, capture_raw, report_progress):
            if payload.page == 2:
                self.calls.append(payload)
                if failure == "failed":
                    raise RuntimeError("fixture page failure")
                result = {
                    "sections": {},
                    "section_errors": {
                        "search_results": {
                            "error_type": "rate_limit",
                            "error_message": "fixture limit",
                        }
                    },
                }
                await capture_raw(
                    {"structuredContent": result, "content": [], "isError": False}, None
                )
                return result
            return await super().execute(payload, capture_raw, report_progress)

    executor = FailingPage({1: ["ada"]})
    app = create_app(settings(tmp_path / "failure.db"), queue_executor=executor)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        payload = setup(client)
        root = client.post("/api/searches", json={**payload, "paginate": True}).json()[
            "search_run_id"
        ]
        assert completed(app, root) == failure
        assert [p.page for p in executor.calls] == [1, 2]
        pool = client.get(
            "/api/candidate-pool", params={"session_id": payload["session_id"]}
        ).json()
        assert [p["username"] for p in pool] == ["ada"]
