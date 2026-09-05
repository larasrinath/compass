from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient
from linkedin_dashboard.db.models import (
    CandidateScore,
    Job,
    ProfileFetch,
    SearchDownload,
)
from linkedin_dashboard.main import create_app
from linkedin_dashboard.queue.jobs import PersonProfilePayload, SearchPeoplePayload
from sqlalchemy import func, select
from test_discovery import brief_payload, settings, start_session, wait_for_job


class SearchAndProfileExecutor:
    def __init__(self):
        self.calls = []
        self.people = ["ada", "grace"]
        self.fail = False

    async def execute(self, payload, capture_raw, report_progress):
        self.calls.append(payload)
        if isinstance(payload, SearchPeoplePayload):
            result = {
                "url": "https://www.linkedin.com/search/results/people/",
                "sections": {"search_results": "Ada and Grace"},
                "references": {
                    "search_results": [
                        {"kind": "person", "url": f"/in/{name}/", "text": name}
                        for name in self.people
                    ]
                },
            }
        else:
            assert isinstance(payload, PersonProfilePayload)
            if self.fail:
                raise RuntimeError("fixture retrieval failure")
            result = {
                "url": f"https://www.linkedin.com/in/{payload.linkedin_username}/",
                "sections": {
                    "main_profile": (
                        f"{payload.linkedin_username}\nPlatform Engineer\nChicago"
                    ),
                    "experience": (
                        "Experience\nPlatform Engineer\nAcme\n"
                        "2020 - Present\nPython Kubernetes"
                    ),
                },
                "references": {},
            }
        await capture_raw(
            {"structuredContent": result, "content": [], "isError": False}, None
        )
        return result


def setup(client):
    session_id = start_session(client)
    brief = client.post("/api/briefs", json=brief_payload(session_id)).json()
    return {"session_id": session_id, "brief_id": brief["id"], "keywords": "Python"}


def settled(app, run_id, count):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        with app.state.database.sessions() as db:
            intent = db.get(SearchDownload, run_id)
            active = db.scalar(
                select(func.count(Job.id)).where(
                    Job.state.in_(["queued", "running", "pending"])
                )
            )
            fetches = db.scalar(select(func.count(ProfileFetch.id)))
            if intent and intent.dispatched_at and not active and fetches == count:
                return
        time.sleep(0.02)
    raise AssertionError("automatic downloads did not settle")


@pytest.mark.parametrize("network", [["F"], ["S"], ["O"], None])
def test_search_downloads_only_its_results_and_rescores(tmp_path, network):
    executor = SearchAndProfileExecutor()
    app = create_app(settings(tmp_path / "auto.db"), queue_executor=executor)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        payload = setup(client)
        # A historical search contributes somebody outside the new search's scope.
        executor.people = ["older"]
        old = client.post("/api/searches", json=payload).json()
        wait_for_job(app, old["job_id"])
        executor.people = ["ada", "grace", "ada"]
        run = client.post(
            "/api/searches",
            json={**payload, "network": network, "automatic_downloads": True},
        ).json()
        settled(app, run["search_run_id"], 2)
        search_call = [p for p in executor.calls if isinstance(p, SearchPeoplePayload)][
            -1
        ]
        assert search_call.network == network
        profiles = [p for p in executor.calls if isinstance(p, PersonProfilePayload)]
        assert {p.linkedin_username for p in profiles} == {"ada", "grace"}
        assert all(p.sections == ["experience"] for p in profiles)
        pool = client.get(
            "/api/candidate-pool", params={"session_id": payload["session_id"]}
        ).json()
        assert (
            next(c for c in pool if c["username"] == "older")["stage"] == "discovered"
        )
        assert all(c["stage"] == "stage1" for c in pool if c["username"] != "older")
        for c in pool:
            if c["username"] != "older":
                with app.state.database.sessions() as db:
                    score = db.scalar(
                        select(CandidateScore).where(
                            CandidateScore.candidate_id == c["id"],
                            CandidateScore.is_current.is_(True),
                        )
                    )
                    assert score.calculation_status == "scored"
        # Repeated searches reuse saved evidence, never redownload it.
        again = client.post(
            "/api/searches", json={**payload, "automatic_downloads": True}
        ).json()
        settled(app, again["search_run_id"], 2)
        assert (
            len([p for p in executor.calls if isinstance(p, PersonProfilePayload)]) == 2
        )


def test_old_search_requires_explicit_catchup_and_failure_is_not_looped(tmp_path):
    executor = SearchAndProfileExecutor()
    executor.fail = True
    app = create_app(settings(tmp_path / "old.db"), queue_executor=executor)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        payload = setup(client)
        run = client.post("/api/searches", json=payload).json()
        wait_for_job(app, run["job_id"])
        assert not any(isinstance(p, PersonProfilePayload) for p in executor.calls)
        endpoint = f"/api/searches/{run['search_run_id']}/downloads"
        assert client.post(endpoint).status_code == 202
        settled(app, run["search_run_id"], 2)
        assert client.post(endpoint).status_code == 202
        assert (
            len([p for p in executor.calls if isinstance(p, PersonProfilePayload)]) == 2
        )


def test_batch_limit_is_enforced_before_database_work(tmp_path):
    app = create_app(
        settings(tmp_path / "limit.db"), queue_executor=SearchAndProfileExecutor()
    )
    with pytest.raises(ValueError, match="1000"):
        asyncio.run(
            app.state.enrichment_service.enqueue_batch(
                [str(i) for i in range(1001)], ["experience"]
            )
        )


def test_download_queue_survives_restart_and_respects_pause(tmp_path):
    from linkedin_dashboard.db.models import QueueControl

    config = settings(tmp_path / "restart.db")
    executor = SearchAndProfileExecutor()
    app = create_app(config, queue_executor=executor)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        payload = setup(client)
        run = client.post("/api/searches", json=payload).json()
        wait_for_job(app, run["job_id"])
        with app.state.database.sessions.begin() as db:
            control = db.get(QueueControl, 1)
            control.state = "paused"
            control.pause_reason = "operator"
            control.operator_resume_required = True
        assert (
            client.post(f"/api/searches/{run['search_run_id']}/downloads").status_code
            == 202
        )
        assert not any(isinstance(p, PersonProfilePayload) for p in executor.calls)
    second_executor = SearchAndProfileExecutor()
    second = create_app(config, queue_executor=second_executor)
    with TestClient(second, base_url="http://127.0.0.1") as client:
        assert client.get("/api/queue/status").json()["state"] == "paused"
        assert second_executor.calls == []
        assert client.post("/api/queue/resume").status_code == 200
        settled(second, run["search_run_id"], 2)
        assert len(second_executor.calls) == 2
        assert all(isinstance(p, PersonProfilePayload) for p in second_executor.calls)


def test_one_thousand_profile_batch_reserves_exact_cost_and_stays_sequential(tmp_path):
    from functools import partial

    from linkedin_dashboard.db.models import (
        Candidate,
        DashboardSession,
        NavigationReservation,
        QueueControl,
    )

    executor = SearchAndProfileExecutor()
    app = create_app(settings(tmp_path / "thousand.db"), queue_executor=executor)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        session_id = start_session(client)
        ids = [f"person-{n}" for n in range(1000)]
        with app.state.database.sessions.begin() as db:
            control = db.get(QueueControl, 1)
            control.state = "paused"
            control.pause_reason = "operator"
            control.operator_resume_required = True
            db.add_all(
                [
                    Candidate(
                        id=name,
                        session_id=session_id,
                        username=name,
                        profile_url=f"https://www.linkedin.com/in/{name}",
                        display_name=name,
                        first_seen_at="2026-09-05T00:00:00+00:00",
                        stage="discovered",
                        retrieval_status="pending",
                    )
                    for name in ids
                ]
            )
        assert client.portal is not None
        jobs = client.portal.call(
            partial(
                app.state.enrichment_service.enqueue_batch,
                ids,
                ["experience"],
                authorize_profile_reads=True,
                new_only=True,
            )
        )
        assert len(jobs) == 1000
        with app.state.database.sessions() as db:
            assert db.get(DashboardSession, session_id).nav_budget == 2000
            assert db.scalar(select(func.sum(NavigationReservation.cost))) == 2000
            assert db.scalar(select(func.count(ProfileFetch.id))) == 1000
            assert (
                db.scalar(select(func.count(Job.id)).where(Job.state == "running")) == 0
            )
        assert executor.calls == []


def test_projection_failure_recovers_intent_without_repeating_search(tmp_path):
    executor = SearchAndProfileExecutor()
    app = create_app(settings(tmp_path / "projection.db"), queue_executor=executor)
    processor = app.state.search_service.processor
    original = processor._parse_search
    first = True

    def fail_once(*args):
        nonlocal first
        if first:
            first = False
            raise RuntimeError("fixture local projection interruption")
        return original(*args)

    processor._parse_search = fail_once
    with TestClient(app, base_url="http://127.0.0.1") as client:
        payload = setup(client)
        run = client.post(
            "/api/searches", json={**payload, "automatic_downloads": True}
        ).json()
        settled(app, run["search_run_id"], 2)
        assert (
            len([p for p in executor.calls if isinstance(p, SearchPeoplePayload)]) == 1
        )
