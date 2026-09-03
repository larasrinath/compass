from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from linkedin_dashboard.db.migrations import (
    v0010_takeover_guards,
    v0011_purged_evidence_ancestry,
)
from linkedin_dashboard.db.models import Candidate, DashboardSession, MessageDraft
from linkedin_dashboard.db.session import Database
from sqlalchemy import text

NOW = "2026-09-02T12:00:00+00:00"
LATER = "2026-09-02T12:05:00+00:00"


def _seed_candidate(database: Database, suffix: str) -> tuple[str, str]:
    candidate_id = f"candidate-{suffix}"
    draft_id = f"draft-{suffix}"
    with database.sessions.begin() as session:
        session.add(
            DashboardSession(
                id=f"session-{suffix}",
                created_at=NOW,
                label="Takeover 4",
                purge_after=LATER,
                nav_budget=120,
                nav_used=0,
                send_enabled=False,
            )
        )
        session.flush()
        session.add(
            Candidate(
                id=candidate_id,
                session_id=f"session-{suffix}",
                username=f"person-{suffix}",
                profile_url=f"https://www.linkedin.com/in/person-{suffix}/",
                first_seen_at=NOW,
                stage="discovered",
                retrieval_status="pending",
            )
        )
        session.flush()
        session.add(
            MessageDraft(
                id=draft_id,
                candidate_id=candidate_id,
                version=1,
                body="Hello",
                body_sha256="a" * 64,
                char_count=5,
                generator="manual",
                grounding_status="pass",
                grounding_report={},
                created_at=NOW,
            )
        )
    return candidate_id, draft_id


def _seed_evidence(database: Database, suffix: str, *, purged: bool = True) -> str:
    _seed_candidate(database, suffix)
    evidence_id = f"evidence-{suffix}"
    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO role_brief "
            "(id, session_id, version, created_at, job_description, target_titles, "
            "location, industries, positive_keywords, negative_keywords, message_tone, "
            "weights_version) VALUES "
            f"('brief-{suffix}', 'session-{suffix}', 1, 'now', 'job', '[]', "
            "'anywhere', '[]', '[]', '[]', 'plain', 'v1')"
        )
        connection.exec_driver_sql(
            "INSERT INTO score "
            "(id, candidate_id, brief_id, weights_version, stage, score, score_lower, "
            "score_upper, confidence, confidence_band, computed_at, is_current) VALUES "
            f"('score-{suffix}', 'candidate-{suffix}', 'brief-{suffix}', 'v1', "
            "'provisional', 1, 1, 1, 1, 'high', 'now', 1)"
        )
        connection.exec_driver_sql(
            "INSERT INTO score_signal "
            "(id, score_id, signal_id, weight, verdict, raw_subscore, contribution, "
            "availability) VALUES "
            f"('signal-{suffix}', 'score-{suffix}', 'skill', 1, 'matched', 1, 1, 1)"
        )
        connection.execute(
            text(
                "INSERT INTO evidence "
                "(id, score_signal_id, section_name, span_start, span_end, snippet, "
                "matcher, matched_term, polarity) VALUES "
                "(:id, :signal, 'experience', 0, 6, 'secret', 'exact', 'secret', "
                "'supporting')"
            ),
            {"id": evidence_id, "signal": f"signal-{suffix}"},
        )
        if purged:
            connection.execute(
                text(
                    "UPDATE evidence SET snippet='[purged]', matched_term='[purged]', "
                    "parsed_field_id=NULL, purged_at=:purged WHERE id=:id"
                ),
                {"purged": LATER, "id": evidence_id},
            )
    return evidence_id


@pytest.mark.parametrize("recursive_triggers", ["ON", "OFF"])
def test_unapproved_purged_evidence_rejects_replace_and_delete(
    database: Database, recursive_triggers: str
) -> None:
    evidence_id = _seed_evidence(database, f"purged-{recursive_triggers.lower()}")
    path = database.path
    database.dispose()

    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA recursive_triggers={recursive_triggers}")
        row = connection.execute(
            "SELECT score_signal_id FROM evidence WHERE id=?", (evidence_id,)
        ).fetchone()
        assert row is not None
        with pytest.raises(sqlite3.IntegrityError, match="purged evidence"):
            connection.execute(
                "INSERT OR REPLACE INTO evidence "
                "(id, score_signal_id, section_name, span_start, span_end, snippet, "
                "matcher, matched_term, polarity) VALUES (?, ?, 'experience', 0, 6, "
                "'restored', 'exact', 'restored', 'supporting')",
                (evidence_id, row[0]),
            )
        with pytest.raises(sqlite3.IntegrityError, match="purged evidence"):
            connection.execute("DELETE FROM evidence WHERE id=?", (evidence_id,))


@pytest.mark.parametrize("recursive_triggers", ["ON", "OFF"])
def test_update_replace_cannot_overwrite_purged_evidence(
    database: Database, recursive_triggers: str
) -> None:
    protected = _seed_evidence(database, f"protected-{recursive_triggers.lower()}")
    attacker = _seed_evidence(
        database, f"attacker-{recursive_triggers.lower()}", purged=False
    )
    path = database.path
    database.dispose()

    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA recursive_triggers={recursive_triggers}")
        with pytest.raises(sqlite3.IntegrityError, match="purged evidence"):
            connection.execute(
                "UPDATE OR REPLACE evidence SET id=? WHERE id=?",
                (protected, attacker),
            )


@pytest.mark.parametrize("recursive_triggers", ["ON", "OFF"])
def test_draft_claim_collision_preserves_referenced_claim(
    database: Database, recursive_triggers: str
) -> None:
    target_candidate, target_draft = _seed_candidate(
        database, f"claim-target-{recursive_triggers.lower()}"
    )
    _, attacker_draft = _seed_candidate(
        database, f"claim-attacker-{recursive_triggers.lower()}"
    )
    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO draft_claim (id, draft_id, claim_text, grounded) VALUES "
            f"('protected-claim', '{target_draft}', 'Protected', 1), "
            f"('attacker-claim', '{attacker_draft}', 'Attacker', 1)"
        )
        connection.execute(
            text(
                "INSERT INTO send_confirmation "
                "(token, candidate_id, draft_id, body_sha256, created_at, expires_at) "
                "VALUES (:token, :candidate, :draft, :hash, :created, :expires)"
            ),
            {
                "token": f"claim-token-{recursive_triggers.lower()}",
                "candidate": target_candidate,
                "draft": target_draft,
                "hash": "a" * 64,
                "created": NOW,
                "expires": LATER,
            },
        )
    path = database.path
    database.dispose()

    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA recursive_triggers={recursive_triggers}")
        with pytest.raises(sqlite3.IntegrityError, match="referenced draft_claim"):
            connection.execute(
                "UPDATE OR REPLACE draft_claim SET id='protected-claim' "
                "WHERE id='attacker-claim'"
            )
        assert connection.execute(
            "SELECT claim_text FROM draft_claim WHERE id='protected-claim'"
        ).fetchone() == ("Protected",)


def test_unapproved_purged_evidence_allows_full_session_purge(
    database: Database,
) -> None:
    evidence_id = _seed_evidence(database, "full-purge")
    with database.engine.begin() as connection:
        connection.exec_driver_sql("DELETE FROM session WHERE id='session-full-purge'")
        assert (
            connection.execute(
                text("SELECT COUNT(*) FROM evidence WHERE id=:id"), {"id": evidence_id}
            ).scalar_one()
            == 0
        )


def _schema_objects(path: Path) -> list[tuple[str, str, str]]:
    with sqlite3.connect(path) as connection:
        return connection.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE type IN ('index', 'trigger') AND sql IS NOT NULL "
            "ORDER BY type, name"
        ).fetchall()


@pytest.mark.parametrize(
    "failure_after", range(1, len(v0010_takeover_guards.STATEMENTS) + 1)
)
def test_v0010_each_statement_is_atomic_and_retryable(
    tmp_path: Path, monkeypatch, failure_after: int
) -> None:
    database = Database(tmp_path / f"interrupted-v10-{failure_after}.db")
    database.initialize()
    with database.engine.begin() as connection:
        connection.execute(
            text("DELETE FROM schema_migration WHERE version=:version"),
            {"version": v0010_takeover_guards.VERSION},
        )
        for name in (
            "purged_evidence_insert_collision",
            "purged_evidence_update_collision",
            "purged_evidence_no_direct_delete",
            "referenced_draft_claim_update_collision",
        ):
            connection.exec_driver_sql(f'DROP TRIGGER "{name}"')
    baseline = _schema_objects(database.path)
    database.dispose()
    retry = Database(database.path)
    original_apply = v0010_takeover_guards.apply

    def interrupted_apply(connection) -> None:
        for index, statement in enumerate(v0010_takeover_guards.STATEMENTS, start=1):
            connection.exec_driver_sql(statement)
            if index == failure_after:
                raise RuntimeError(f"interrupted after v10 statement {index}")

    monkeypatch.setattr(v0010_takeover_guards, "apply", interrupted_apply)
    with pytest.raises(RuntimeError, match=f"v10 statement {failure_after}"):
        retry.initialize()
    assert _schema_objects(retry.path) == baseline

    monkeypatch.setattr(v0010_takeover_guards, "apply", original_apply)
    retry.initialize()
    retry.dispose()


@pytest.mark.parametrize("recursive_triggers", ["ON", "OFF"])
@pytest.mark.parametrize(
    ("table", "row_id"),
    [
        ("score_signal", "signal"),
        ("score", "score"),
        ("candidate", "candidate"),
        ("role_brief", "brief"),
    ],
)
def test_purged_evidence_blocks_every_ancestor_delete(
    database: Database,
    recursive_triggers: str,
    table: str,
    row_id: str,
) -> None:
    suffix = f"ancestor-{table}-{recursive_triggers.lower()}"
    evidence_id = _seed_evidence(database, suffix)
    path = database.path
    database.dispose()

    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA recursive_triggers={recursive_triggers}")
        with pytest.raises(sqlite3.IntegrityError, match="purged evidence"):
            connection.execute(
                f'DELETE FROM "{table}" WHERE id=?', (f"{row_id}-{suffix}",)
            )
        assert connection.execute(
            "SELECT purged_at FROM evidence WHERE id=?", (evidence_id,)
        ).fetchone() == (LATER,)


@pytest.mark.parametrize("recursive_triggers", ["ON", "OFF"])
@pytest.mark.parametrize(
    ("table", "row_id"),
    [
        ("score_signal", "signal"),
        ("score", "score"),
        ("candidate", "candidate"),
        ("role_brief", "brief"),
    ],
)
def test_replace_cannot_remove_purged_evidence_through_ancestor(
    database: Database,
    recursive_triggers: str,
    table: str,
    row_id: str,
) -> None:
    suffix = f"replace-{table}-{recursive_triggers.lower()}"
    evidence_id = _seed_evidence(database, suffix)
    path = database.path
    database.dispose()

    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA recursive_triggers={recursive_triggers}")
        with pytest.raises(sqlite3.IntegrityError, match="purged evidence"):
            connection.execute(
                f'INSERT OR REPLACE INTO "{table}" SELECT * FROM "{table}" WHERE id=?',
                (f"{row_id}-{suffix}",),
            )
        assert connection.execute(
            "SELECT purged_at FROM evidence WHERE id=?", (evidence_id,)
        ).fetchone() == (LATER,)


@pytest.mark.parametrize("recursive_triggers", ["ON", "OFF"])
@pytest.mark.parametrize(
    ("table", "row_id"),
    [
        ("score_signal", "signal"),
        ("score", "score"),
        ("candidate", "candidate"),
        ("role_brief", "brief"),
    ],
)
def test_update_replace_cannot_reuse_purged_ancestor_identity(
    database: Database,
    recursive_triggers: str,
    table: str,
    row_id: str,
) -> None:
    target_suffix = f"update-target-{table}-{recursive_triggers.lower()}"
    attacker_suffix = f"update-attacker-{table}-{recursive_triggers.lower()}"
    protected_evidence = _seed_evidence(database, target_suffix)
    _seed_evidence(database, attacker_suffix, purged=False)
    path = database.path
    database.dispose()

    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA recursive_triggers={recursive_triggers}")
        with pytest.raises(sqlite3.IntegrityError, match="purged evidence"):
            connection.execute(
                f'UPDATE OR REPLACE "{table}" SET id=? WHERE id=?',
                (f"{row_id}-{target_suffix}", f"{row_id}-{attacker_suffix}"),
            )
        assert connection.execute(
            "SELECT purged_at FROM evidence WHERE id=?", (protected_evidence,)
        ).fetchone() == (LATER,)


@pytest.mark.parametrize("recursive_triggers", ["ON", "OFF"])
def test_full_session_purge_can_cross_all_tombstone_guards(
    database: Database,
    recursive_triggers: str,
) -> None:
    suffix = f"guarded-full-purge-{recursive_triggers.lower()}"
    evidence_id = _seed_evidence(database, suffix)
    path = database.path
    database.dispose()

    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA recursive_triggers={recursive_triggers}")
        connection.execute("DELETE FROM session WHERE id=?", (f"session-{suffix}",))
        assert connection.execute(
            "SELECT COUNT(*) FROM evidence WHERE id=?", (evidence_id,)
        ).fetchone() == (0,)


@pytest.mark.parametrize(
    "failure_after", range(1, len(v0011_purged_evidence_ancestry.STATEMENTS) + 1)
)
def test_v0011_each_statement_is_atomic_and_retryable(
    tmp_path: Path, monkeypatch, failure_after: int
) -> None:
    database = Database(tmp_path / f"interrupted-v11-{failure_after}.db")
    database.initialize()
    with database.engine.begin() as connection:
        connection.execute(
            text("DELETE FROM schema_migration WHERE version=:version"),
            {"version": v0011_purged_evidence_ancestry.VERSION},
        )
        for name in v0011_purged_evidence_ancestry.TRIGGER_NAMES:
            connection.exec_driver_sql(f'DROP TRIGGER "{name}"')
    baseline = _schema_objects(database.path)
    database.dispose()
    retry = Database(database.path)
    original_apply = v0011_purged_evidence_ancestry.apply

    def interrupted_apply(connection) -> None:
        for index, statement in enumerate(
            v0011_purged_evidence_ancestry.STATEMENTS, start=1
        ):
            connection.exec_driver_sql(statement)
            if index == failure_after:
                raise RuntimeError(f"interrupted after v11 statement {index}")

    monkeypatch.setattr(v0011_purged_evidence_ancestry, "apply", interrupted_apply)
    with pytest.raises(RuntimeError, match=f"v11 statement {failure_after}"):
        retry.initialize()
    assert _schema_objects(retry.path) == baseline

    monkeypatch.setattr(v0011_purged_evidence_ancestry, "apply", original_apply)
    retry.initialize()
    retry.dispose()
