from __future__ import annotations

# Integrity fixtures intentionally keep each SQL mutation readable as one statement.
# ruff: noqa: E501
import json
import sqlite3
from pathlib import Path

import pytest
from linkedin_dashboard.db.session import Database


def _database(tmp_path: Path) -> Path:
    path = tmp_path / "integrity.db"
    Database(path).initialize()
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        for suffix in ("1", "2"):
            connection.execute(
                "INSERT INTO session VALUES (?, ?, ?, ?, 120, 0, 0)",
                (
                    f"session-{suffix}",
                    "2026-01-01T00:00:00+00:00",
                    suffix,
                    "2027-01-01T00:00:00+00:00",
                ),
            )
            connection.execute(
                "INSERT INTO role_brief "
                "(id, session_id, version, created_at, sealed_at, superseded_at, "
                "job_description, target_titles, location, industries, "
                "positive_keywords, negative_keywords, message_tone, weights_version) "
                "VALUES (?, ?, 1, ?, NULL, NULL, ?, ?, '', ?, ?, ?, '', 'v1')",
                (
                    f"brief-{suffix}",
                    f"session-{suffix}",
                    "2026-01-01T00:00:00+00:00",
                    "Platform role",
                    json.dumps(["Engineer"]),
                    json.dumps(["Software"]),
                    json.dumps(["platform"]),
                    json.dumps([]),
                ),
            )
            connection.execute(
                "INSERT INTO brief_skill VALUES (?, ?, 'Python', 'required', '[]', 0)",
                (f"skill-{suffix}", f"brief-{suffix}"),
            )
            connection.execute(
                "INSERT INTO brief_term VALUES (?, ?, 'target_title', 'Engineer', 'engineer', '[]', 0)",
                (f"term-{suffix}", f"brief-{suffix}"),
            )
            connection.execute(
                "UPDATE role_brief SET sealed_at=created_at WHERE id=?",
                (f"brief-{suffix}",),
            )
            payload = json.dumps(
                {"keywords": "platform", "search_run_id": f"run-{suffix}"}
            )
            connection.execute(
                "INSERT INTO job VALUES (?, ?, 'search_people', ?, 'done', 0, 2, ?, ?, ?, NULL, ?, NULL)",
                (
                    f"job-{suffix}",
                    f"session-{suffix}",
                    payload,
                    "2026-01-01T00:00:00+00:00",
                    "2026-01-01T00:00:00+00:00",
                    "2026-01-01T00:00:00+00:00",
                    f"correlation-{suffix}",
                ),
            )
            connection.execute(
                "INSERT INTO search_run VALUES (?, ?, ?, ?, ?, 'platform', NULL, NULL, NULL, ?, ?, NULL, 1, 1, 'queued')",
                (
                    f"run-{suffix}",
                    f"session-{suffix}",
                    f"brief-{suffix}",
                    f"job-{suffix}",
                    "2026-01-01T00:00:00+00:00",
                    "https://www.linkedin.com/search/results/people/",
                    json.dumps({"structuredContent": {"references": {}}}),
                ),
            )
            connection.execute(
                "INSERT INTO candidate (id, session_id, username, profile_url, display_name, profile_urn, first_seen_at, stage, retrieval_status) "
                "VALUES (?, ?, ?, ?, ?, NULL, ?, 'discovered', 'pending')",
                (
                    f"candidate-{suffix}",
                    f"session-{suffix}",
                    f"Alice{suffix}",
                    f"https://www.linkedin.com/in/Alice{suffix}",
                    f"Alice {suffix}",
                    "2026-01-01T00:00:00+00:00",
                ),
            )
            connection.execute(
                "INSERT INTO candidate_ref VALUES (?, ?, 'person', ?, ?, NULL, NULL, '{}', 0)",
                (
                    f"ref-{suffix}",
                    f"run-{suffix}",
                    f"/in/Alice{suffix}/",
                    f"Alice {suffix}",
                ),
            )
            connection.execute(
                "INSERT INTO candidate_source VALUES (?, ?, ?)",
                (f"candidate-{suffix}", f"run-{suffix}", f"ref-{suffix}"),
            )
            connection.execute(
                "UPDATE search_run SET processed_at=?, status='ok' WHERE id=?",
                ("2026-01-01T00:00:01+00:00", f"run-{suffix}"),
            )
    return path


def test_candidate_keys_are_database_derived_and_url_identity_is_unique(
    tmp_path: Path,
) -> None:
    path = _database(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError, match="dedupe key must match"):
            connection.execute(
                "INSERT INTO candidate (id, session_id, username, dedupe_key, profile_url, first_seen_at, stage, retrieval_status) "
                "VALUES ('forged', 'session-1', 'Bob', 'not-bob', 'https://www.linkedin.com/in/Bob', ?, 'discovered', 'pending')",
                ("2026-01-01T00:00:00+00:00",),
            )
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            connection.execute(
                "INSERT INTO candidate (id, session_id, username, profile_url, first_seen_at, stage, retrieval_status) "
                "VALUES ('case-copy', 'session-1', 'alice1', 'https://www.linkedin.com/in/else', ?, 'discovered', 'pending')",
                ("2026-01-01T00:00:00+00:00",),
            )
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            connection.execute(
                "INSERT INTO candidate (id, session_id, username, profile_url, first_seen_at, stage, retrieval_status) "
                "VALUES ('url-copy', 'session-1', 'Different', 'HTTPS://WWW.LINKEDIN.COM/IN/ALICE1/', ?, 'discovered', 'pending')",
                ("2026-01-01T00:00:00+00:00",),
            )


@pytest.mark.parametrize("recursive", ["OFF", "ON"])
def test_cross_session_and_cross_run_provenance_is_rejected_even_on_replace(
    tmp_path: Path, recursive: str
) -> None:
    path = _database(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA recursive_triggers={recursive}")
        with pytest.raises(sqlite3.IntegrityError, match="ownership mismatch"):
            connection.execute(
                "INSERT OR REPLACE INTO candidate_source VALUES ('candidate-1', 'run-1', 'ref-2')"
            )
        with pytest.raises(sqlite3.IntegrityError, match="ownership mismatch"):
            connection.execute(
                "INSERT INTO candidate_source VALUES ('candidate-1', 'run-2', 'ref-2')"
            )
        with pytest.raises(sqlite3.IntegrityError, match="ownership mismatch"):
            connection.execute(
                "INSERT INTO search_run VALUES ('forged-run', 'session-1', 'brief-1', 'job-2', ?, 'x', NULL, NULL, NULL, NULL, NULL, NULL, 0, 0, 'queued')",
                ("2026-01-01T00:00:00+00:00",),
            )


def test_brief_and_processed_discovery_history_are_immutable_but_session_purges(
    tmp_path: Path,
) -> None:
    path = _database(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        mutations = [
            "UPDATE role_brief SET job_description='rewrite' WHERE id='brief-1'",
            "UPDATE role_brief SET id='rewritten-brief' WHERE id='brief-1'",
            "UPDATE role_brief SET session_id='session-2' WHERE id='brief-1'",
            "INSERT INTO brief_skill VALUES ('late-skill', 'brief-1', 'Go', 'optional', '[]', 1)",
            "INSERT INTO brief_term VALUES ('late-term', 'brief-1', 'industry', 'Media', 'media', '[]', 1)",
            "DELETE FROM brief_skill WHERE id='skill-1'",
            "DELETE FROM brief_term WHERE id='term-1'",
            "UPDATE search_run SET status='failed' WHERE id='run-1'",
            "UPDATE job SET payload='{}' WHERE id='job-1'",
            "DELETE FROM job WHERE id='job-1'",
            "UPDATE candidate_ref SET text='rewrite' WHERE id='ref-1'",
            "INSERT INTO candidate_ref VALUES ('late-ref', 'run-1', 'person', '/in/late/', 'Late', NULL, NULL, '{}', 1)",
            "UPDATE candidate_source SET candidate_ref_id='ref-2' WHERE candidate_id='candidate-1' AND search_run_id='run-1'",
            "UPDATE candidate SET session_id='session-2' WHERE id='candidate-1'",
            "DELETE FROM candidate_ref WHERE id='ref-1'",
            "DELETE FROM candidate_source WHERE candidate_id='candidate-1' AND search_run_id='run-1'",
        ]
        for statement in mutations:
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(statement)

        connection.execute("DELETE FROM session WHERE id='session-1'")
        assert connection.execute(
            "SELECT count(*) FROM role_brief WHERE session_id='session-1'"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM candidate WHERE session_id='session-1'"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM search_run WHERE session_id='session-1'"
        ).fetchone() == (0,)
