from __future__ import annotations

import os
import stat

import pytest
from linkedin_dashboard.db.models import (
    Candidate,
    DashboardSession,
    MessageDraft,
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


def test_finished_attempt_is_immutable_and_resolution_is_final(
    database: Database,
) -> None:
    candidate_id, draft_id = seed_candidate(database, "immutable")
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
                text("UPDATE send_attempt SET state='FAILED_CONCLUSIVE' WHERE id=:id"),
                {"id": "attempt-immutable"},
            )

    with database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE send_attempt SET resolution='confirmed_not_sent', "
                "resolved_at=:at, resolution_note='checked LinkedIn' WHERE id=:id"
            ),
            {"at": NOW, "id": "attempt-immutable"},
        )

    with pytest.raises(DBAPIError, match="already set"):
        with database.engine.begin() as connection:
            connection.execute(
                text("UPDATE send_attempt SET resolution_note='changed' WHERE id=:id"),
                {"id": "attempt-immutable"},
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
