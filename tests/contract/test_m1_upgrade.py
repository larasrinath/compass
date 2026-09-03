from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from linkedin_dashboard.db.migrations import v0017_role_discovery
from linkedin_dashboard.db.session import Database

M1_HEAD = "320de376f126551391bcfacaa926ad77d4705c47"


def _m1_source(tmp_path: Path) -> Path:
    project = Path(__file__).resolve().parents[2]
    probe = subprocess.run(
        ["git", "cat-file", "-e", f"{M1_HEAD}^{{commit}}"],
        cwd=project,
        capture_output=True,
        check=False,
    )
    if probe.returncode != 0:
        pytest.skip(f"historical M1 commit {M1_HEAD} is unavailable")
    archive = tmp_path / "m1.tar"
    subprocess.run(
        ["git", "archive", "--format=tar", f"--output={archive}", M1_HEAD],
        cwd=project,
        check=True,
    )
    source = tmp_path / "m1-source"
    source.mkdir(mode=0o700)
    subprocess.run(["tar", "-xf", archive, "-C", source], check=True)
    return source


def _create_authentic_m1_database(tmp_path: Path) -> Path:
    source = _m1_source(tmp_path)
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    path = data / "session.db"
    environment = {**os.environ, "PYTHONPATH": str(source / "backend")}
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; from linkedin_dashboard.db.session import "
            "Database; Database(Path(__import__('sys').argv[1])).initialize()",
            str(path),
        ],
        cwd=source,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return path


def _seed_m1_history(path: Path) -> None:
    values = {
        "session": "session-m1",
        "brief": "brief-m1",
        "skill": "skill-m1",
        "run": "run-m1",
        "candidate": "candidate-m1",
        "ref": "reference-m1",
    }
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO session VALUES (?, ?, ?, ?, 120, 1, 0)",
            (
                values["session"],
                "2026-01-01T00:00:00+00:00",
                "M1",
                "2027-01-01T00:00:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO role_brief VALUES (?, ?, 1, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                values["brief"],
                values["session"],
                "2026-01-01T00:00:00+00:00",
                "Legacy platform role",
                json.dumps(["Platform Engineer"]),
                "Chicago",
                json.dumps(["Fintech"]),
                json.dumps(["Kubernetes"]),
                json.dumps([]),
                "Direct",
                "v1",
            ),
        )
        connection.execute(
            "INSERT INTO brief_skill VALUES (?, ?, ?, ?, ?)",
            (
                values["skill"],
                values["brief"],
                "Kubernetes",
                "required",
                json.dumps(["k8s"]),
            ),
        )
        connection.execute(
            "INSERT INTO search_run VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                values["run"],
                values["session"],
                values["brief"],
                "2026-01-01T00:01:00+00:00",
                "platform engineer",
                "Chicago",
                json.dumps(["F"]),
                "1115",
                "https://www.linkedin.com/search/results/people/",
                json.dumps({"legacy": True}),
                1,
                1,
                "ok",
            ),
        )
        connection.execute(
            "INSERT INTO candidate VALUES "
            "(?, ?, ?, ?, ?, NULL, ?, 'discovered', 'pending')",
            (
                values["candidate"],
                values["session"],
                "Alice",
                "https://www.linkedin.com/in/Alice",
                "Alice Example",
                "2026-01-01T00:01:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO candidate_ref VALUES (?, ?, 'person', ?, ?, ?, NULL, 0)",
            (
                values["ref"],
                values["run"],
                "/in/Alice/",
                "Alice Example",
                "Legacy search context",
            ),
        )
        connection.execute(
            "INSERT INTO candidate_source VALUES (?, ?, ?)",
            (values["candidate"], values["run"], values["ref"]),
        )


def test_authentic_m1_upgrade_is_atomic_retryable_and_preserves_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _create_authentic_m1_database(tmp_path)
    _seed_m1_history(path)
    original = v0017_role_discovery._migrate_brief_terms

    def fail_after_rebuild(_connection: object) -> None:
        raise RuntimeError("injected migration failure")

    monkeypatch.setattr(
        v0017_role_discovery, "_migrate_brief_terms", fail_after_rebuild
    )
    database = Database(path)
    with pytest.raises(RuntimeError, match="injected migration failure"):
        database.initialize()

    with sqlite3.connect(path) as connection:
        candidate_columns = {
            row[1] for row in connection.execute("PRAGMA table_xinfo(candidate)")
        }
        assert "dedupe_key" not in candidate_columns
        assert (
            connection.execute(
                "SELECT 1 FROM schema_migration WHERE version=?",
                (v0017_role_discovery.VERSION,),
            ).fetchone()
            is None
        )
        assert connection.execute(
            "SELECT count(*) FROM candidate_source"
        ).fetchone() == (1,)

    monkeypatch.setattr(v0017_role_discovery, "_migrate_brief_terms", original)
    database.initialize()

    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        candidate = connection.execute("SELECT * FROM candidate").fetchone()
        assert candidate is not None
        assert candidate["username"] == "Alice"
        assert candidate["dedupe_key"] == "alice"
        assert (
            connection.execute("SELECT count(*) FROM candidate_source").fetchone()[0]
            == 1
        )
        run = connection.execute("SELECT * FROM search_run").fetchone()
        assert run is not None
        assert json.loads(run["raw_response"]) == {"legacy": True}
        assert run["processed_at"] == "2026-01-01T00:01:00+00:00"
        job = connection.execute(
            "SELECT * FROM job WHERE id=?", (run["job_id"],)
        ).fetchone()
        assert job is not None and job["session_id"] == run["session_id"]
        objects = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('index','trigger')"
            )
        }
        assert "uq_candidate_username" not in objects
        assert "candidate_session_dedupe_key" in objects


def test_authentic_m1_upgrade_preflights_inconsistent_provenance(
    tmp_path: Path,
) -> None:
    path = _create_authentic_m1_database(tmp_path)
    _seed_m1_history(path)
    # Model an old database damaged while foreign-key enforcement was disabled.
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            "UPDATE candidate SET session_id='wrong-session' WHERE id='candidate-m1'"
        )

    with pytest.raises(RuntimeError, match="inconsistent candidate provenance"):
        Database(path).initialize()

    with sqlite3.connect(path) as connection:
        assert "dedupe_key" not in {
            row[1] for row in connection.execute("PRAGMA table_xinfo(candidate)")
        }
        assert (
            connection.execute(
                "SELECT 1 FROM schema_migration WHERE version=?",
                (v0017_role_discovery.VERSION,),
            ).fetchone()
            is None
        )
