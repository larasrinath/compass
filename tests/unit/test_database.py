from __future__ import annotations

import os
import stat

import pytest
from linkedin_dashboard.db.migrations import v0001_constraints, v0002_integrity
from linkedin_dashboard.db.models import (
    Base,
    Candidate,
    CandidateScore,
    DashboardSession,
    DraftClaim,
    MessageDraft,
    RoleBrief,
    SendAttempt,
)
from linkedin_dashboard.db.session import Database, get_journal_mode
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

NOW = "2026-09-02T12:00:00+00:00"


def seed_candidate(database: Database, suffix: str) -> tuple[str, str]:
    session_id = f"session-{suffix}"
    candidate_id = f"candidate-{suffix}"
    draft_id = f"draft-{suffix}"
    with database.sessions.begin() as db_session:
        db_session.add(
            DashboardSession(
                id=session_id,
                created_at=NOW,
                label="Test session",
                purge_after=NOW,
                nav_budget=120,
                nav_used=0,
                send_enabled=False,
            )
        )
        db_session.flush()
        db_session.add(
            Candidate(
                id=candidate_id,
                session_id=session_id,
                username=f"person-{suffix}",
                profile_url=f"https://www.linkedin.com/in/person-{suffix}/",
                first_seen_at=NOW,
                stage="discovered",
                retrieval_status="pending",
            )
        )
        db_session.flush()
        db_session.add(
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


def attempt(
    *,
    attempt_id: str,
    candidate_id: str,
    draft_id: str,
    state: str,
    confirm_send: bool,
    resolution: str = "unresolved",
    finished_at: str | None = None,
) -> SendAttempt:
    return SendAttempt(
        id=attempt_id,
        candidate_id=candidate_id,
        draft_id=draft_id,
        idempotency_key=(attempt_id + "0" * 64)[:64],
        body_sha256="a" * 64,
        confirm_send=confirm_send,
        state=state,
        started_at=NOW,
        finished_at=finished_at,
        resolution=resolution,
        resolved_at=NOW if resolution != "unresolved" else None,
        resolution_note="checked" if resolution != "unresolved" else None,
    )


def test_database_uses_wal_and_owner_only_permissions(database: Database) -> None:
    with database.engine.connect() as connection:
        assert get_journal_mode(connection).casefold() == "wal"

    assert stat.S_IMODE(os.stat(database.path).st_mode) == 0o600
    assert database.writable()


def test_existing_database_parent_permissions_are_unchanged(tmp_path) -> None:
    parent = tmp_path / "shared-parent"
    parent.mkdir(mode=0o755)
    os.chmod(parent, 0o755)
    database = Database(parent / "dashboard.db")

    try:
        database.initialize()
    finally:
        database.dispose()

    assert stat.S_IMODE(os.stat(parent).st_mode) == 0o755


def test_new_database_directories_are_owner_only(tmp_path) -> None:
    first = tmp_path / "private"
    second = first / "nested"
    database = Database(second / "dashboard.db")

    try:
        database.initialize()
    finally:
        database.dispose()

    assert stat.S_IMODE(os.stat(first).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(second).st_mode) == 0o700


def test_existing_v0001_database_receives_integrity_migration(tmp_path) -> None:
    database = Database(tmp_path / "upgrade.db")
    Base.metadata.create_all(database.engine)
    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE schema_migration "
            "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        v0001_constraints.apply(connection)
        connection.execute(
            text(
                "INSERT INTO schema_migration(version, applied_at) "
                "VALUES (:version, :applied_at)"
            ),
            {"version": v0001_constraints.VERSION, "applied_at": NOW},
        )

    try:
        database.initialize()
        with database.engine.connect() as connection:
            versions = list(
                connection.execute(
                    text("SELECT version FROM schema_migration ORDER BY version")
                ).scalars()
            )
            trigger_sql = connection.execute(
                text(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type='trigger' AND name='send_attempt_is_immutable'"
                )
            ).scalar_one()
    finally:
        database.dispose()

    assert versions == [v0001_constraints.VERSION, v0002_integrity.VERSION]
    assert "NEW.candidate_id IS NOT OLD.candidate_id" in trigger_sql


def test_partial_unique_index_rejects_a_second_live_send(database: Database) -> None:
    candidate_id, draft_id = seed_candidate(database, "live")
    with database.sessions.begin() as db_session:
        db_session.add(
            attempt(
                attempt_id="attempt-live-1",
                candidate_id=candidate_id,
                draft_id=draft_id,
                state="SENDING",
                confirm_send=True,
            )
        )

    with pytest.raises(IntegrityError):
        with database.sessions.begin() as db_session:
            db_session.add(
                attempt(
                    attempt_id="attempt-live-2",
                    candidate_id=candidate_id,
                    draft_id=draft_id,
                    state="SENDING",
                    confirm_send=True,
                )
            )


def test_ambiguous_dry_run_does_not_block_a_real_send(database: Database) -> None:
    candidate_id, draft_id = seed_candidate(database, "dry")
    with database.sessions.begin() as db_session:
        db_session.add_all(
            [
                attempt(
                    attempt_id="attempt-dry-1",
                    candidate_id=candidate_id,
                    draft_id=draft_id,
                    state="AMBIGUOUS",
                    confirm_send=False,
                    finished_at=NOW,
                ),
                attempt(
                    attempt_id="attempt-dry-2",
                    candidate_id=candidate_id,
                    draft_id=draft_id,
                    state="SENDING",
                    confirm_send=True,
                ),
            ]
        )


def test_direct_sql_cannot_bypass_live_send_index_with_non_boolean(
    database: Database,
) -> None:
    candidate_id, draft_id = seed_candidate(database, "boolean-index")
    with database.sessions.begin() as db_session:
        db_session.add(
            attempt(
                attempt_id="attempt-boolean-live",
                candidate_id=candidate_id,
                draft_id=draft_id,
                state="SENDING",
                confirm_send=True,
            )
        )

    with pytest.raises(DBAPIError, match="invalid boolean"):
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO send_attempt "
                    "(id, candidate_id, draft_id, idempotency_key, body_sha256, "
                    "confirm_send, state, started_at, resolution) "
                    "SELECT :new_id, candidate_id, draft_id, :new_key, body_sha256, "
                    "2, state, started_at, resolution FROM send_attempt WHERE id=:id"
                ),
                {
                    "new_id": "attempt-boolean-bypass",
                    "new_key": "b" * 64,
                    "id": "attempt-boolean-live",
                },
            )


def test_all_persisted_boolean_columns_reject_non_booleans(database: Database) -> None:
    candidate_id, draft_id = seed_candidate(database, "all-booleans")
    with database.sessions.begin() as db_session:
        brief = RoleBrief(
            id="brief-all-booleans",
            session_id="session-all-booleans",
            version=1,
            created_at=NOW,
            job_description="Test",
            target_titles=[],
            location="Anywhere",
            industries=[],
            positive_keywords=[],
            negative_keywords=[],
            message_tone="plain",
            weights_version="v1",
        )
        db_session.add(brief)
        db_session.flush()
        db_session.add(
            CandidateScore(
                id="score-all-booleans",
                candidate_id=candidate_id,
                brief_id=brief.id,
                weights_version="v1",
                stage="provisional",
                score=0.0,
                score_lower=0.0,
                score_upper=0.0,
                confidence=0.0,
                confidence_band="low",
                computed_at=NOW,
                is_current=True,
            )
        )
        db_session.add(
            DraftClaim(
                id="claim-all-booleans",
                draft_id=draft_id,
                claim_text="Test",
                grounded=True,
            )
        )
        db_session.add(
            attempt(
                attempt_id="attempt-all-booleans",
                candidate_id=candidate_id,
                draft_id=draft_id,
                state="DRY_RUN_OK",
                confirm_send=False,
            )
        )

    statements = (
        "UPDATE session SET send_enabled=2 WHERE id='session-all-booleans'",
        "UPDATE score SET is_current=2 WHERE id='score-all-booleans'",
        "UPDATE draft_claim SET grounded=2 WHERE id='claim-all-booleans'",
        "UPDATE send_attempt SET confirm_send=2 WHERE id='attempt-all-booleans'",
        "UPDATE send_attempt SET tool_sent=2 WHERE id='attempt-all-booleans'",
        "UPDATE send_attempt SET tool_recipient_selected=2 "
        "WHERE id='attempt-all-booleans'",
    )
    for statement in statements:
        with pytest.raises(DBAPIError, match=r"boolean|CHECK constraint"):
            with database.engine.begin() as connection:
                connection.exec_driver_sql(statement)


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("id", "attempt-reparented"),
        ("candidate_id", "candidate-immutable-target"),
        ("draft_id", "draft-immutable-target"),
        ("idempotency_key", "c" * 64),
        ("body_sha256", "d" * 64),
        ("confirm_send", 0),
        ("state", "FAILED_CONCLUSIVE"),
        ("tool_status", "changed"),
        ("tool_sent", 1),
        ("tool_recipient_selected", 1),
        ("tool_url", "https://www.linkedin.com/changed"),
        ("raw_response", '{"changed":true}'),
        ("error_class", "ChangedError"),
        ("error_message", "changed"),
        ("started_at", "2026-09-02T13:00:00+00:00"),
        ("finished_at", "2026-09-02T13:00:00+00:00"),
    ],
)
def test_finished_attempt_protects_every_non_resolution_column(
    database: Database, column: str, value: object
) -> None:
    candidate_id, draft_id = seed_candidate(database, "immutable")
    seed_candidate(database, "immutable-target")
    with database.sessions.begin() as db_session:
        db_session.add(
            attempt(
                attempt_id="attempt-immutable",
                candidate_id=candidate_id,
                draft_id=draft_id,
                state="AMBIGUOUS",
                confirm_send=True,
                finished_at=NOW,
            )
        )

    with pytest.raises(DBAPIError, match="immutable"):
        with database.engine.begin() as connection:
            connection.execute(
                text(f"UPDATE send_attempt SET {column}=:value WHERE id=:id"),
                {"value": value, "id": "attempt-immutable"},
            )


@pytest.mark.parametrize("column", ["resolved_at", "resolution_note"])
def test_unresolved_attempt_rejects_partial_resolution_updates(
    database: Database, column: str
) -> None:
    candidate_id, draft_id = seed_candidate(database, f"partial-{column}")
    attempt_id = f"attempt-partial-{column}"
    with database.sessions.begin() as db_session:
        db_session.add(
            attempt(
                attempt_id=attempt_id,
                candidate_id=candidate_id,
                draft_id=draft_id,
                state="AMBIGUOUS",
                confirm_send=True,
                finished_at=NOW,
            )
        )

    with pytest.raises(DBAPIError, match="complete transition"):
        with database.engine.begin() as connection:
            connection.execute(
                text(f"UPDATE send_attempt SET {column}=:value WHERE id=:id"),
                {"value": NOW, "id": attempt_id},
            )


def test_resolution_transitions_once_and_is_final(database: Database) -> None:
    candidate_id, draft_id = seed_candidate(database, "resolution")
    with database.sessions.begin() as db_session:
        db_session.add(
            attempt(
                attempt_id="attempt-resolution",
                candidate_id=candidate_id,
                draft_id=draft_id,
                state="AMBIGUOUS",
                confirm_send=True,
                finished_at=NOW,
            )
        )

    with database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE send_attempt SET resolution='confirmed_not_sent', "
                "resolved_at=:at, resolution_note='checked LinkedIn' WHERE id=:id"
            ),
            {"at": NOW, "id": "attempt-resolution"},
        )

    with pytest.raises(DBAPIError, match="already set"):
        with database.engine.begin() as connection:
            connection.execute(
                text("UPDATE send_attempt SET resolution_note='changed' WHERE id=:id"),
                {"id": "attempt-resolution"},
            )

    with database.sessions.begin() as db_session:
        db_session.add(
            attempt(
                attempt_id="attempt-new-after-resolution",
                candidate_id=candidate_id,
                draft_id=draft_id,
                state="SENDING",
                confirm_send=True,
            )
        )
