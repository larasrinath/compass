"""Exercise the complete saved-data workflow through a real local fake MCP."""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path
from typing import Any

import uvicorn
from fastapi.testclient import TestClient
from fastmcp import FastMCP
from linkedin_dashboard.main import create_app
from linkedin_dashboard.settings import Settings


def _finished(client: TestClient, job_id: str) -> str:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        jobs = client.get("/api/jobs").json()
        job = next(row for row in jobs if row["id"] == job_id)
        if job["state"] in {"done", "failed", "interrupted", "cancelled"}:
            return str(job["state"])
        time.sleep(0.02)
    raise AssertionError("Local fake-MCP job did not finish")


def test_download_restart_offline_review_rescore_and_evidence(tmp_path: Path) -> None:
    calls: list[str] = []
    mcp = FastMCP("dashboard-offline-acceptance")
    skills = " ".join(f"skill{index}" for index in range(10))

    @mcp.tool
    def search_people(keywords: str) -> dict[str, Any]:
        calls.append("search_people")
        return {
            "url": "https://www.linkedin.com/search/results/people/",
            "sections": {"search_results": f"Ada Example · {keywords}"},
            "references": {
                "search_results": [
                    {
                        "kind": "person",
                        "url": "/in/ada-example/",
                        "text": "Ada Example",
                    },
                    {
                        "kind": "person",
                        "url": "/in/ADA-EXAMPLE/",
                        "text": "Ada Example",
                    },
                ]
            },
        }

    @mcp.tool
    def get_person_profile(
        linkedin_username: str, sections: str | None = None
    ) -> dict[str, Any]:
        calls.append("get_person_profile")
        result = {"main_profile": "Ada Example\nPlatform Engineer"}
        if sections == "skills":
            result["skills"] = skills
        else:
            result["experience"] = (
                "Platform Engineer\nExample Company\nJan 2020 - Present"
            )
        return {
            "url": f"https://www.linkedin.com/in/{linkedin_username}/",
            "sections": result,
        }

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            mcp.http_app(path="/mcp", stateless_http=True),
            host="127.0.0.1",
            port=port,
            log_level="critical",
            access_log=False,
            timeout_graceful_shutdown=0,
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    settings = Settings(
        db_path=tmp_path / "saved-work.db",
        mcp_url=f"http://127.0.0.1:{port}/mcp",
        inter_call_delay_seconds=0,
    )
    try:
        with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
            session_id = client.post(
                "/api/session", json={"label": "Offline acceptance"}
            ).json()["id"]
            brief_payload = {
                "session_id": session_id,
                "job_description": "Build a reliable platform.",
                "required_skills": [
                    {"term": f"skill{i}", "aliases": []} for i in range(10)
                ],
                "positive_keywords": ["platform"],
                "negative_keywords": [],
            }
            brief = client.post("/api/briefs", json=brief_payload)
            assert brief.status_code == 201, brief.text
            search = client.post(
                "/api/searches",
                json={
                    "session_id": session_id,
                    "brief_id": brief.json()["id"],
                    "keywords": "platform",
                },
            )
            assert search.status_code == 202, search.text
            assert _finished(client, search.json()["job_id"]) == "done"
            pool = client.get(
                "/api/candidate-pool", params={"session_id": session_id}
            ).json()
            assert len(pool) == 1  # case-insensitive dedupe survives persistence
            candidate_id = pool[0]["id"]
            for sections in (["experience"], ["skills"]):
                queued = client.post(
                    f"/api/candidates/{candidate_id}/enrich",
                    json={"sections": sections},
                )
                assert queued.status_code == 202, queued.text
                assert _finished(client, queued.json()["job_id"]) == "done"
            stored = client.get(
                f"/api/candidates/{candidate_id}/sections/skills"
            ).json()
            assert stored["raw_text"] == skills
            assert (
                client.get(
                    "/api/candidates", params={"session_id": session_id}
                ).status_code
                == 409
            )
    finally:
        server.should_exit = True
        thread.join(timeout=5)
    assert not thread.is_alive()
    assert calls == ["search_people", "get_person_profile", "get_person_profile"]

    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        assert client.get("/api/session").json()["id"] == session_id
        assert client.get("/api/mcp/status").json()["reachable"] is False
        searches = client.get("/api/searches", params={"session_id": session_id}).json()
        assert searches[0]["status"] == "ok"
        gate = client.post(
            "/api/session/gates/A",
            json={"note": "Saved names and duplicates reviewed offline."},
        )
        assert gate.status_code == 201, gate.text
        rows = client.get("/api/candidates", params={"session_id": session_id})
        assert rows.status_code == 200 and len(rows.json()) == 1
        previous_score = rows.json()[0]["score_id"]
        config = client.get("/api/weights").json()
        config["weights"]["S-1"] += 1
        changed = client.put(
            "/api/weights/current",
            json={
                "expected_version": config["version"],
                "weights": config["weights"],
                "metro_region_equivalences": config["metro_region_equivalences"],
            },
        )
        assert changed.status_code == 200, changed.text
        brief_payload["job_description"] = "Build and maintain a reliable platform."
        assert client.put("/api/briefs/current", json=brief_payload).status_code == 200
        assert client.post(f"/api/candidates/{candidate_id}/rescore").status_code == 200
        detail = client.get(f"/api/candidates/{candidate_id}").json()
        assert detail["score"]["score_id"] != previous_score
        assert len(detail["score_history"]) >= 2
        assert (
            client.get(f"/api/candidates/{candidate_id}/sections/skills").json()[
                "raw_text"
            ]
            == skills
        )
        evidence = [
            item
            for signal in detail["signals"]
            for claim in signal["claims"]
            for item in claim["evidence"]
            if item["availability"]["state"] == "available"
        ]
        evidence_ids = list(dict.fromkeys(item["id"] for item in evidence))
        assert len(evidence_ids) >= 10
        assert (
            client.post(
                "/api/session/gates/B", json={"evidence_ids": [evidence_ids[0]] * 10}
            ).status_code
            == 422
        )
        accepted = client.post(
            "/api/session/gates/B",
            json={
                "evidence_ids": evidence_ids[:10],
                "note": "Ten current exact spans checked offline.",
            },
        )
        assert accepted.status_code == 201, accepted.text
        # A requested download with the connector offline must not erase saved work.
        queued = client.post(
            f"/api/candidates/{candidate_id}/enrich", json={"sections": ["skills"]}
        )
        assert queued.status_code == 202, queued.text
        assert client.post("/api/queue/resume").status_code == 200
        assert _finished(client, queued.json()["job_id"]) == "failed"
        assert (
            client.get(f"/api/candidates/{candidate_id}/sections/skills").json()[
                "raw_text"
            ]
            == skills
        )

    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        saved = client.get("/api/session").json()
        assert set(saved["phase_gates"]) == {"A", "B"}
        assert set(saved["phase_gates"]["B"]["evidence_ids"]) == set(evidence_ids[:10])
        assert (
            len(client.get("/api/candidates", params={"session_id": session_id}).json())
            == 1
        )
        assert (
            client.get(f"/api/candidates/{candidate_id}/sections/skills").json()[
                "raw_text"
            ]
            == skills
        )
    assert calls == ["search_people", "get_person_profile", "get_person_profile"]
