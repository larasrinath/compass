from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from linkedin_dashboard.db.migrations import (
    v0010_takeover_guards,
    v0011_purged_evidence_ancestry,
    v0012_score_session_provenance,
    v0013_history_root_immutability,
    v0014_history_identity_completion,
)
from linkedin_dashboard.db.models import Candidate, DashboardSession, MessageDraft
from linkedin_dashboard.db.session import Database
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

NOW = "2026-09-02T12:00:00+00:00"
LATER = "2026-09-02T12:05:00+00:00"


@contextmanager
def _migration_test_phase(database: Database) -> Iterator[None]:
    """Use an isolated non-runtime engine to construct historical fixtures."""
    runtime_engine = database.engine
    migration_engine = create_engine(
        URL.create("sqlite", database=str(database.path)), poolclass=NullPool
    )

    @event.listens_for(migration_engine, "connect")
    def configure(connection, record) -> None:
        del record
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA recursive_triggers=ON")

    database.engine = migration_engine
    database.sessions.configure(bind=migration_engine)
    try:
        yield
    finally:
        database.sessions.configure(bind=runtime_engine)
        database.engine = runtime_engine
        migration_engine.dispose()


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
    with _migration_test_phase(database), database.engine.begin() as connection:
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
        with pytest.raises(
            sqlite3.IntegrityError,
            match=r"purged evidence|score identity.*immutable",
        ):
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
        with pytest.raises(
            sqlite3.IntegrityError,
            match=r"purged evidence|score identity.*immutable",
        ):
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
    with _migration_test_phase(database), database.engine.begin() as connection:
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


def _seed_two_score_sessions(database: Database, suffix: str) -> None:
    _seed_candidate(database, f"{suffix}-a")
    _seed_candidate(database, f"{suffix}-b")
    with database.engine.begin() as connection:
        for side in ("a", "b"):
            connection.exec_driver_sql(
                "INSERT INTO role_brief "
                "(id, session_id, version, created_at, job_description, "
                "target_titles, location, industries, positive_keywords, "
                "negative_keywords, message_tone, weights_version) VALUES "
                f"('brief-{suffix}-{side}', 'session-{suffix}-{side}', 1, "
                "'now', 'job', '[]', 'anywhere', '[]', '[]', '[]', "
                "'plain', 'v1')"
            )


def _score_insert_sql(*, replace: bool = False) -> str:
    operation = "INSERT OR REPLACE" if replace else "INSERT"
    return (
        f"{operation} INTO score "
        "(id, candidate_id, brief_id, weights_version, stage, score, "
        "score_lower, score_upper, confidence, confidence_band, computed_at, "
        "is_current) VALUES (?, ?, ?, 'v1', 'provisional', 1, 1, 1, 1, "
        "'high', 'now', 1)"
    )


@pytest.mark.parametrize("recursive_triggers", ["ON", "OFF"])
def test_score_pair_must_share_session_for_insert_update_and_upsert(
    database: Database,
    recursive_triggers: str,
) -> None:
    suffix = f"score-session-{recursive_triggers.lower()}"
    _seed_two_score_sessions(database, suffix)
    path = database.path
    database.dispose()

    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA recursive_triggers={recursive_triggers}")
        with pytest.raises(
            sqlite3.IntegrityError,
            match=r"must share a session|roots are immutable|identity.*immutable",
        ):
            connection.execute(
                _score_insert_sql(),
                (
                    f"score-cross-{suffix}",
                    f"candidate-{suffix}-a",
                    f"brief-{suffix}-b",
                ),
            )

        valid_id = f"score-valid-{suffix}"
        connection.execute(
            _score_insert_sql(),
            (valid_id, f"candidate-{suffix}-a", f"brief-{suffix}-a"),
        )
        with pytest.raises(
            sqlite3.IntegrityError,
            match=r"must share a session|roots are immutable|identity.*immutable",
        ):
            connection.execute(
                "UPDATE OR REPLACE score SET brief_id=? WHERE id=?",
                (f"brief-{suffix}-b", valid_id),
            )
        with pytest.raises(
            sqlite3.IntegrityError,
            match=r"must share a session|roots are immutable|identity.*immutable",
        ):
            connection.execute(
                _score_insert_sql(replace=True),
                (valid_id, f"candidate-{suffix}-a", f"brief-{suffix}-b"),
            )
        with pytest.raises(sqlite3.IntegrityError, match="cross a score session"):
            connection.execute(
                "UPDATE OR REPLACE candidate SET session_id=? WHERE id=?",
                (f"session-{suffix}-b", f"candidate-{suffix}-a"),
            )
        with pytest.raises(sqlite3.IntegrityError, match="cross a score session"):
            connection.execute(
                "UPDATE OR REPLACE role_brief SET session_id=? WHERE id=?",
                (f"session-{suffix}-b", f"brief-{suffix}-a"),
            )
        assert connection.execute(
            "SELECT candidate_id, brief_id FROM score WHERE id=?", (valid_id,)
        ).fetchone() == (f"candidate-{suffix}-a", f"brief-{suffix}-a")


def test_v0012_preflight_rejects_existing_cross_session_scores_atomically(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "v12-preflight.db")
    database.initialize()
    with _migration_test_phase(database), database.engine.begin() as connection:
        connection.execute(
            text("DELETE FROM schema_migration WHERE version=:version"),
            {"version": v0012_score_session_provenance.VERSION},
        )
        for name in v0012_score_session_provenance.TRIGGER_NAMES:
            connection.exec_driver_sql(f'DROP TRIGGER "{name}"')
        connection.execute(
            text("DELETE FROM schema_migration WHERE version=:version"),
            {"version": v0013_history_root_immutability.VERSION},
        )
        for name in v0013_history_root_immutability.TRIGGER_NAMES:
            connection.exec_driver_sql(f'DROP TRIGGER "{name}"')
    with _migration_test_phase(database):
        _seed_two_score_sessions(database, "preflight")
        with database.engine.begin() as connection:
            connection.exec_driver_sql(
                _score_insert_sql(),
                (
                    "score-preflight",
                    "candidate-preflight-a",
                    "brief-preflight-b",
                ),
            )
    baseline = _schema_objects(database.path)
    database.dispose()

    retry = Database(database.path)
    with pytest.raises(RuntimeError, match="sessions differ"):
        retry.initialize()
    assert _schema_objects(retry.path) == baseline
    with sqlite3.connect(retry.path) as connection:
        assert connection.execute(
            "SELECT candidate_id, brief_id FROM score WHERE id='score-preflight'"
        ).fetchone() == ("candidate-preflight-a", "brief-preflight-b")
        connection.execute(
            "UPDATE score SET brief_id='brief-preflight-a' WHERE id='score-preflight'"
        )

    retry.initialize()
    retry.dispose()


@pytest.mark.parametrize(
    "failure_after", range(1, len(v0012_score_session_provenance.STATEMENTS) + 1)
)
def test_v0012_each_statement_is_atomic_and_retryable(
    tmp_path: Path, monkeypatch, failure_after: int
) -> None:
    database = Database(tmp_path / f"interrupted-v12-{failure_after}.db")
    database.initialize()
    with _migration_test_phase(database), database.engine.begin() as connection:
        connection.execute(
            text("DELETE FROM schema_migration WHERE version=:version"),
            {"version": v0012_score_session_provenance.VERSION},
        )
        for name in v0012_score_session_provenance.TRIGGER_NAMES:
            connection.exec_driver_sql(f'DROP TRIGGER "{name}"')
    baseline = _schema_objects(database.path)
    database.dispose()
    retry = Database(database.path)
    original_apply = v0012_score_session_provenance.apply

    def interrupted_apply(connection) -> None:
        v0012_score_session_provenance._preflight(connection)
        for index, statement in enumerate(
            v0012_score_session_provenance.STATEMENTS, start=1
        ):
            connection.exec_driver_sql(statement)
            if index == failure_after:
                raise RuntimeError(f"interrupted after v12 statement {index}")

    monkeypatch.setattr(v0012_score_session_provenance, "apply", interrupted_apply)
    with pytest.raises(RuntimeError, match=f"v12 statement {failure_after}"):
        retry.initialize()
    assert _schema_objects(retry.path) == baseline

    monkeypatch.setattr(v0012_score_session_provenance, "apply", original_apply)
    retry.initialize()
    retry.dispose()


@pytest.mark.parametrize(
    "session_root_query",
    [
        "SELECT candidate.session_id FROM score "
        "JOIN candidate ON candidate.id = score.candidate_id WHERE score.id=?",
        "SELECT role_brief.session_id FROM score "
        "JOIN role_brief ON role_brief.id = score.brief_id WHERE score.id=?",
    ],
)
def test_full_purge_uses_same_owning_session_from_either_score_root(
    database: Database,
    session_root_query: str,
) -> None:
    suffix = str(abs(hash(session_root_query)))
    evidence_id = _seed_evidence(database, f"same-session-purge-{suffix}")
    score_id = f"score-same-session-purge-{suffix}"
    path = database.path
    database.dispose()

    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        session_id = connection.execute(session_root_query, (score_id,)).fetchone()
        assert session_id is not None
        connection.execute("DELETE FROM session WHERE id=?", session_id)
        assert connection.execute(
            "SELECT COUNT(*) FROM evidence WHERE id=?", (evidence_id,)
        ).fetchone() == (0,)


@pytest.mark.parametrize("recursive_triggers", ["ON", "OFF"])
@pytest.mark.parametrize("history", ["confirmation", "attempt"])
def test_candidate_session_freezes_for_approved_or_attempted_message_history(
    database: Database, recursive_triggers: str, history: str
) -> None:
    suffix = f"history-session-{history}-{recursive_triggers.lower()}"
    candidate_id, draft_id = _seed_candidate(database, suffix)
    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO session "
            "(id, created_at, label, purge_after, nav_budget, nav_used, send_enabled) "
            f"VALUES ('session-target-{suffix}', 'now', 'Target', 'later', 120, 0, 0)"
        )
        if history == "confirmation":
            connection.execute(
                text(
                    "INSERT INTO send_confirmation "
                    "(token, candidate_id, draft_id, body_sha256, created_at, "
                    "expires_at) VALUES (:token, :candidate, :draft, :hash, "
                    ":created, :expires)"
                ),
                {
                    "token": f"token-{suffix}",
                    "candidate": candidate_id,
                    "draft": draft_id,
                    "hash": "a" * 64,
                    "created": NOW,
                    "expires": LATER,
                },
            )
        else:
            connection.execute(
                text(
                    "INSERT INTO send_attempt "
                    "(id, candidate_id, draft_id, idempotency_key, body_sha256, "
                    "confirm_send, state, started_at, finished_at, resolution) "
                    "VALUES (:id, :candidate, :draft, :key, :hash, 0, "
                    "'DRY_RUN_OK', :started, :finished, 'unresolved')"
                ),
                {
                    "id": f"attempt-{suffix}",
                    "candidate": candidate_id,
                    "draft": draft_id,
                    "key": f"attempt-{suffix}".ljust(64, "0")[:64],
                    "hash": "a" * 64,
                    "started": NOW,
                    "finished": LATER,
                },
            )
    path = database.path
    database.dispose()

    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA recursive_triggers={recursive_triggers}")
        with pytest.raises(sqlite3.IntegrityError, match="session is immutable"):
            connection.execute(
                "UPDATE OR REPLACE candidate SET session_id=? WHERE id=?",
                (f"session-target-{suffix}", candidate_id),
            )
        assert connection.execute(
            "SELECT session_id FROM candidate WHERE id=?", (candidate_id,)
        ).fetchone() == (f"session-{suffix}",)


def test_candidate_history_session_freeze_is_enforced_on_managed_connection(
    database: Database,
) -> None:
    candidate_id, draft_id = _seed_candidate(database, "managed-history-session")
    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO session "
            "(id, created_at, label, purge_after, nav_budget, nav_used, send_enabled) "
            "VALUES ('managed-target', 'now', 'Target', 'later', 120, 0, 0)"
        )
        connection.exec_driver_sql(
            "INSERT INTO send_confirmation "
            "(token, candidate_id, draft_id, body_sha256, created_at, expires_at) "
            f"VALUES ('managed-token', '{candidate_id}', '{draft_id}', "
            f"'{'a' * 64}', '{NOW}', '{LATER}')"
        )

    with pytest.raises(DBAPIError, match="session is immutable"):
        with database.engine.begin() as connection:
            connection.execute(
                text("UPDATE candidate SET session_id='managed-target' WHERE id=:id"),
                {"id": candidate_id},
            )


@pytest.mark.parametrize("recursive_triggers", ["ON", "OFF"])
@pytest.mark.parametrize("root", ["candidate_id", "brief_id"])
@pytest.mark.parametrize("operation", ["update", "upsert", "replace"])
def test_score_roots_are_immutable_for_updates_and_real_upserts(
    database: Database, recursive_triggers: str, root: str, operation: str
) -> None:
    suffix = f"score-root-{root}-{operation}-{recursive_triggers.lower()}"
    _seed_candidate(database, suffix)
    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO candidate "
            "(id, session_id, username, profile_url, first_seen_at, stage, "
            "retrieval_status) VALUES "
            f"('candidate-alt-{suffix}', 'session-{suffix}', 'person-alt-{suffix}', "
            f"'https://www.linkedin.com/in/person-alt-{suffix}/', 'now', "
            "'discovered', 'pending')"
        )
        for name in ("base", "alt"):
            connection.exec_driver_sql(
                "INSERT INTO role_brief "
                "(id, session_id, version, created_at, job_description, "
                "target_titles, location, industries, positive_keywords, "
                "negative_keywords, message_tone, weights_version) VALUES "
                f"('brief-{name}-{suffix}', 'session-{suffix}', "
                f"{1 if name == 'base' else 2}, 'now', 'job', '[]', 'anywhere', "
                "'[]', '[]', '[]', 'plain', 'v1')"
            )
        connection.exec_driver_sql(
            _score_insert_sql().replace(
                "VALUES (?, ?, ?",
                "VALUES ("
                f"'score-{suffix}', 'candidate-{suffix}', 'brief-base-{suffix}'",
            )
        )
    path = database.path
    database.dispose()
    replacement = (
        f"candidate-alt-{suffix}" if root == "candidate_id" else f"brief-alt-{suffix}"
    )

    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA recursive_triggers={recursive_triggers}")
        with pytest.raises(
            sqlite3.IntegrityError,
            match=r"roots are immutable|identity.*immutable",
        ):
            if operation == "update":
                connection.execute(
                    f"UPDATE score SET {root}=? WHERE id=?",
                    (replacement, f"score-{suffix}"),
                )
            elif operation == "upsert":
                candidate = (
                    replacement if root == "candidate_id" else f"candidate-{suffix}"
                )
                brief = replacement if root == "brief_id" else f"brief-base-{suffix}"
                connection.execute(
                    "INSERT INTO score "
                    "(id, candidate_id, brief_id, weights_version, stage, score, "
                    "score_lower, score_upper, confidence, confidence_band, "
                    "computed_at, is_current) VALUES (?, ?, ?, 'v1', "
                    "'provisional', 1, 1, 1, 1, 'high', 'now', 1) "
                    "ON CONFLICT(id) DO UPDATE SET "
                    "candidate_id=excluded.candidate_id, brief_id=excluded.brief_id",
                    (f"score-{suffix}", candidate, brief),
                )
            else:
                candidate = (
                    replacement if root == "candidate_id" else f"candidate-{suffix}"
                )
                brief = replacement if root == "brief_id" else f"brief-base-{suffix}"
                connection.execute(
                    _score_insert_sql(replace=True),
                    (f"score-{suffix}", candidate, brief),
                )
        assert connection.execute(
            "SELECT candidate_id, brief_id FROM score WHERE id=?", (f"score-{suffix}",)
        ).fetchone() == (f"candidate-{suffix}", f"brief-base-{suffix}")


@pytest.mark.parametrize(
    "failure_after", range(1, len(v0013_history_root_immutability.STATEMENTS) + 1)
)
def test_v0013_each_statement_is_atomic_and_retryable(
    tmp_path: Path, monkeypatch, failure_after: int
) -> None:
    database = Database(tmp_path / f"interrupted-v13-{failure_after}.db")
    database.initialize()
    with _migration_test_phase(database), database.engine.begin() as connection:
        connection.execute(
            text("DELETE FROM schema_migration WHERE version=:version"),
            {"version": v0013_history_root_immutability.VERSION},
        )
        for name in v0013_history_root_immutability.TRIGGER_NAMES:
            connection.exec_driver_sql(f'DROP TRIGGER "{name}"')
    baseline = _schema_objects(database.path)
    database.dispose()
    retry = Database(database.path)
    original_apply = v0013_history_root_immutability.apply

    def interrupted_apply(connection) -> None:
        for index, statement in enumerate(
            v0013_history_root_immutability.STATEMENTS, start=1
        ):
            connection.exec_driver_sql(statement)
            if index == failure_after:
                raise RuntimeError(f"interrupted after v13 statement {index}")

    monkeypatch.setattr(v0013_history_root_immutability, "apply", interrupted_apply)
    with pytest.raises(RuntimeError, match=f"v13 statement {failure_after}"):
        retry.initialize()
    assert _schema_objects(retry.path) == baseline

    monkeypatch.setattr(v0013_history_root_immutability, "apply", original_apply)
    retry.initialize()
    retry.dispose()


@pytest.mark.parametrize("recursive_triggers", ["ON", "OFF"])
def test_update_replace_score_id_collision_preserves_both_histories(
    database: Database, recursive_triggers: str
) -> None:
    target_suffix = f"score-id-target-{recursive_triggers.lower()}"
    attacker_suffix = f"score-id-attacker-{recursive_triggers.lower()}"
    target_evidence = _seed_evidence(database, target_suffix, purged=False)
    attacker_evidence = _seed_evidence(database, attacker_suffix, purged=False)
    path = database.path
    database.dispose()

    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA recursive_triggers={recursive_triggers}")
        with pytest.raises(sqlite3.IntegrityError, match="score identity is immutable"):
            connection.execute(
                "UPDATE OR REPLACE score SET id=? WHERE id=?",
                (f"score-{target_suffix}", f"score-{attacker_suffix}"),
            )
        assert connection.execute(
            "SELECT id FROM score WHERE id IN (?, ?) ORDER BY id",
            (f"score-{target_suffix}", f"score-{attacker_suffix}"),
        ).fetchall() == sorted(
            [(f"score-{target_suffix}",), (f"score-{attacker_suffix}",)]
        )
        assert connection.execute(
            "SELECT id FROM evidence WHERE id IN (?, ?) ORDER BY id",
            (target_evidence, attacker_evidence),
        ).fetchall() == sorted([(target_evidence,), (attacker_evidence,)])


@pytest.mark.parametrize(
    "failure_after", range(1, len(v0014_history_identity_completion.STATEMENTS) + 1)
)
def test_v0014_each_statement_is_atomic_and_retryable(
    tmp_path: Path, monkeypatch, failure_after: int
) -> None:
    database = Database(tmp_path / f"interrupted-v14-{failure_after}.db")
    database.initialize()
    with _migration_test_phase(database), database.engine.begin() as connection:
        connection.execute(
            text("DELETE FROM schema_migration WHERE version=:version"),
            {"version": v0014_history_identity_completion.VERSION},
        )
        for name in v0014_history_identity_completion.TRIGGER_NAMES:
            connection.exec_driver_sql(f'DROP TRIGGER "{name}"')
    baseline = _schema_objects(database.path)
    database.dispose()
    retry = Database(database.path)
    original_apply = v0014_history_identity_completion.apply

    def interrupted_apply(connection) -> None:
        for index, statement in enumerate(
            v0014_history_identity_completion.STATEMENTS, start=1
        ):
            connection.exec_driver_sql(statement)
            if index == failure_after:
                raise RuntimeError(f"interrupted after v14 statement {index}")

    monkeypatch.setattr(v0014_history_identity_completion, "apply", interrupted_apply)
    with pytest.raises(RuntimeError, match=f"v14 statement {failure_after}"):
        retry.initialize()
    assert _schema_objects(retry.path) == baseline

    monkeypatch.setattr(v0014_history_identity_completion, "apply", original_apply)
    retry.initialize()
    retry.dispose()
