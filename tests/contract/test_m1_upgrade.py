from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from linkedin_dashboard.db.migrations import (
    v0017_role_discovery,
    v0018_candidate_identity,
    v0021_m3_final_integrity,
)
from linkedin_dashboard.db.session import Database
from linkedin_dashboard.db.unicode_identity import register_sqlite_unicode_casefold
from sqlalchemy.engine import Connection

M1_HEAD = "320de376f126551391bcfacaa926ad77d4705c47"
M2_REJECTED_HEAD = "aa8656b952598af10db880e34f5a8dd6445b127a"
M2_FINAL_HEAD = "a8f78b4fc07228f7ec9b7c03db1e154d98b4c313"


def _source_at_head(tmp_path: Path, head: str, label: str) -> Path:
    project = Path(__file__).resolve().parents[2]
    probe = subprocess.run(
        ["git", "cat-file", "-e", f"{head}^{{commit}}"],
        cwd=project,
        capture_output=True,
        check=False,
    )
    if probe.returncode != 0:
        pytest.skip(f"historical {label} commit {head} is unavailable")
    archive = tmp_path / f"{label}.tar"
    subprocess.run(
        ["git", "archive", "--format=tar", f"--output={archive}", head],
        cwd=project,
        check=True,
    )
    source = tmp_path / f"{label}-source"
    source.mkdir(mode=0o700)
    subprocess.run(["tar", "-xf", archive, "-C", source], check=True)
    return source


def _m1_source(tmp_path: Path) -> Path:
    return _source_at_head(tmp_path, M1_HEAD, "m1")


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


def _create_authentic_m2_database(tmp_path: Path) -> Path:
    source = _source_at_head(tmp_path, M2_REJECTED_HEAD, "m2-rejected")
    data = tmp_path / "m2-data"
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


def _create_authentic_final_m2_database(tmp_path: Path) -> Path:
    source = _source_at_head(tmp_path, M2_FINAL_HEAD, "m2-final")
    data = tmp_path / "m2-final-data"
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


def _seed_final_m2_profile_history(path: Path) -> None:
    raw = {
        "structuredContent": {
            "url": "https://www.linkedin.com/in/legacy/",
            "sections": {"main_profile": "Legacy\nEngineer\nChicago"},
        }
    }
    with sqlite3.connect(path) as connection:
        register_sqlite_unicode_casefold(connection)
        connection.execute(
            "INSERT INTO session VALUES "
            "('m2-final-session','now','M2 final','later',120,2,0)"
        )
        connection.execute(
            "INSERT INTO candidate "
            "(id,session_id,username,profile_url,display_name,profile_urn,first_seen_at,"
            "stage,retrieval_status) VALUES "
            "('m2-final-candidate','m2-final-session','legacy',"
            "'https://www.linkedin.com/in/legacy','Legacy Person',"
            "'urn:li:fsd_profile:legacy','now','stage1','ok')"
        )
        connection.execute(
            "INSERT INTO job "
            "(id,session_id,kind,payload,state,attempts,max_attempts,queued_at,"
            "started_at,finished_at,error,correlation_id,claim_token) VALUES "
            "('m2-final-job','m2-final-session','get_person_profile',?,"
            "'done',1,2,'now','now','now',NULL,'legacy-correlation',NULL)",
            (json.dumps({"linkedin_username": "legacy", "sections": ["experience"]}),),
        )
        connection.execute(
            "INSERT INTO job_attempt "
            "(id,job_id,attempt_number,worker_token,started_at,response_received_at,"
            "finished_at,outcome,raw_response,raw_error,error_class,safe_error_message,"
            "retry_at) VALUES "
            "('m2-final-attempt','m2-final-job',1,'legacy-worker','now','now','now',"
            "'ok',?,NULL,NULL,NULL,NULL)",
            (json.dumps(raw),),
        )
        connection.execute(
            "INSERT INTO profile_fetch "
            "(id,candidate_id,job_id,tool,requested_sections,args,started_at,"
            "finished_at,duration_ms,outcome,raw_response,returned_url) VALUES "
            "('m2-final-fetch','m2-final-candidate','m2-final-job',"
            "'get_person_profile',?,?,'now','now',1,'ok',?,"
            "'https://www.linkedin.com/in/legacy/')",
            (
                json.dumps(["main_profile", "experience"]),
                json.dumps({"linkedin_username": "legacy", "sections": ["experience"]}),
                json.dumps(raw),
            ),
        )
        connection.execute(
            "INSERT INTO parsed_field VALUES "
            "('m2-final-field','m2-final-candidate','headline','Engineer',"
            "'main_profile',7,15,'Engineer','deterministic','legacy','now')"
        )
        connection.execute(
            "INSERT INTO section_reference VALUES "
            "('m2-final-reference','m2-final-candidate','main_profile','person',"
            "'/in/legacy/','Legacy Person',NULL,NULL)"
        )


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
        assert "dedupe_key" not in candidate.keys()
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
        assert "candidate_session_username_casefold" in objects


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


def test_authentic_m1_upgrade_preflights_unicode_casefold_duplicates(
    tmp_path: Path,
) -> None:
    path = _create_authentic_m1_database(tmp_path)
    _seed_m1_history(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE candidate SET username='Straße', "
            "profile_url='https://www.linkedin.com/in/Straße' "
            "WHERE id='candidate-m1'"
        )
        connection.execute(
            "INSERT INTO candidate VALUES "
            "(?, ?, ?, ?, ?, NULL, ?, 'discovered', 'pending')",
            (
                "candidate-m1-duplicate",
                "session-m1",
                "STRASSE",
                "https://www.linkedin.com/in/STRASSE",
                "Duplicate",
                "2026-01-01T00:02:00+00:00",
            ),
        )

    with pytest.raises(RuntimeError, match="duplicate normalized candidate identity"):
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


def test_authentic_rejected_m2_upgrade_drops_auxiliary_key_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _create_authentic_m2_database(tmp_path)
    with sqlite3.connect(path) as connection:
        register_sqlite_unicode_casefold(connection)
        connection.execute(
            "INSERT INTO session VALUES ('m2-session', 'now', 'M2', 'later', 120, 0, 0)"
        )
        connection.execute(
            "INSERT INTO candidate "
            "(id, session_id, username, dedupe_key, profile_url, first_seen_at, "
            "stage, retrieval_status) VALUES "
            "('m2-candidate', 'm2-session', 'Straße', 'strasse', "
            "'https://www.linkedin.com/in/Straße', 'now', 'discovered', 'pending')"
        )

    original = v0018_candidate_identity.apply

    def fail_after_rebuild(connection: Connection) -> None:
        original(connection)
        raise RuntimeError("injected v0018 failure")

    monkeypatch.setattr(v0018_candidate_identity, "apply", fail_after_rebuild)
    database = Database(path)
    with pytest.raises(RuntimeError, match="injected v0018 failure"):
        database.initialize()

    with sqlite3.connect(path) as connection:
        assert "dedupe_key" in {
            row[1] for row in connection.execute("PRAGMA table_xinfo(candidate)")
        }
        assert (
            connection.execute(
                "SELECT 1 FROM schema_migration WHERE version=?",
                (v0018_candidate_identity.VERSION,),
            ).fetchone()
            is None
        )

    monkeypatch.setattr(v0018_candidate_identity, "apply", original)
    database.initialize()
    with sqlite3.connect(path) as connection:
        assert "dedupe_key" not in {
            row[1] for row in connection.execute("PRAGMA table_xinfo(candidate)")
        }
        assert connection.execute(
            "SELECT username FROM candidate WHERE id='m2-candidate'"
        ).fetchone() == ("Straße",)


def test_authentic_populated_final_m2_upgrade_is_canonical_and_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _create_authentic_final_m2_database(tmp_path)
    _seed_final_m2_profile_history(path)
    original = v0021_m3_final_integrity._canonical_rebuilds

    def fail_after_rebuild(connection: Connection) -> None:
        original(connection)
        raise RuntimeError("injected v0021 failure")

    monkeypatch.setattr(
        v0021_m3_final_integrity, "_canonical_rebuilds", fail_after_rebuild
    )
    database = Database(path)
    with pytest.raises(RuntimeError, match="injected v0021 failure"):
        database.initialize()

    with sqlite3.connect(path) as connection:
        assert "profile_urn_quarantined" not in {
            row[1] for row in connection.execute("PRAGMA table_xinfo(candidate)")
        }
        assert "processed_at" not in {
            row[1] for row in connection.execute("PRAGMA table_xinfo(profile_fetch)")
        }
        assert connection.execute("SELECT count(*) FROM candidate").fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM profile_fetch").fetchone() == (
            1,
        )
        assert connection.execute("SELECT count(*) FROM parsed_field").fetchone() == (
            1,
        )
        assert (
            connection.execute(
                "SELECT 1 FROM schema_migration WHERE version=?",
                (v0021_m3_final_integrity.VERSION,),
            ).fetchone()
            is None
        )

    monkeypatch.setattr(v0021_m3_final_integrity, "_canonical_rebuilds", original)
    database.initialize()

    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        candidate = connection.execute(
            "SELECT * FROM candidate WHERE id='m2-final-candidate'"
        ).fetchone()
        fetch = connection.execute(
            "SELECT * FROM profile_fetch WHERE id='m2-final-fetch'"
        ).fetchone()
        attempt = connection.execute(
            "SELECT * FROM job_attempt WHERE id='m2-final-attempt'"
        ).fetchone()
        parsed = connection.execute(
            "SELECT * FROM parsed_field WHERE id='m2-final-field'"
        ).fetchone()
        reference = connection.execute(
            "SELECT * FROM section_reference WHERE id='m2-final-reference'"
        ).fetchone()
        assert candidate is not None
        assert candidate["profile_urn"] == "urn:li:fsd_profile:legacy"
        assert candidate["profile_urn_quarantined"] == 0
        assert fetch is not None
        assert fetch["request_stage"] == "stage1"
        assert fetch["root_fetch_id"] == "m2-final-fetch"
        assert fetch["contract_error"] == "legacy_m2_unprojected"
        assert attempt is not None and attempt["external_call_started_at"] == "now"
        assert parsed is not None and parsed["profile_section_id"] is None
        assert reference is not None
        assert reference["fetch_id"] is None
        assert reference["source_position"] is None
        assert connection.execute("PRAGMA foreign_key_check").fetchone() is None
