from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path

import pytest
from linkedin_dashboard.db.migrations import (
    v0008_history_hardening,
    v0009_integrity_completion,
    v0010_takeover_guards,
    v0011_purged_evidence_ancestry,
    v0012_score_session_provenance,
)
from linkedin_dashboard.db.models import (
    Candidate,
    DashboardSession,
    DraftClaim,
    Evidence,
    MessageDraft,
    SendAttempt,
    SendConfirmation,
)
from linkedin_dashboard.db.session import Database
from sqlalchemy import insert, text
from sqlalchemy.exc import DBAPIError

NOW = "2026-09-02T12:00:00+00:00"
LATER = "2026-09-02T12:05:00+00:00"


def _seed_candidate(database: Database, suffix: str) -> tuple[str, str]:
    session_id = f"session-{suffix}"
    candidate_id = f"candidate-{suffix}"
    draft_id = f"draft-{suffix}"
    with database.sessions.begin() as db_session:
        db_session.add(
            DashboardSession(
                id=session_id,
                created_at=NOW,
                label="Hardening test",
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
                profile_urn=f"urn:li:fsd_profile:{suffix}",
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


def _confirmation(token: str, candidate_id: str, draft_id: str) -> SendConfirmation:
    return SendConfirmation(
        token=token,
        candidate_id=candidate_id,
        draft_id=draft_id,
        body_sha256="a" * 64,
        created_at=NOW,
        expires_at=LATER,
    )


def _attempt(
    attempt_id: str,
    candidate_id: str,
    draft_id: str,
    *,
    state: str = "SENDING",
    confirm_send: bool = True,
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
        finished_at=None if state == "SENDING" else LATER,
        resolution="unresolved",
    )


def _schema_objects(path: Path) -> list[tuple[str, str, str]]:
    with sqlite3.connect(path) as connection:
        return connection.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE type IN ('index', 'trigger') AND sql IS NOT NULL "
            "ORDER BY type, name"
        ).fetchall()


@pytest.mark.parametrize("name", ["dashboard?.db", "dashboard#.db", "dashboard%.db"])
def test_sqlalchemy_url_preserves_special_database_filename(
    tmp_path: Path, name: str
) -> None:
    database = Database(tmp_path / name)
    database.initialize()
    database.dispose()

    assert {path.name for path in tmp_path.iterdir()} == {name}
    with sqlite3.connect(database.path) as connection:
        assert connection.execute(
            "SELECT 1 FROM schema_migration WHERE version=?",
            (v0008_history_hardening.VERSION,),
        ).fetchone() == (1,)


@pytest.mark.parametrize("disabled", ["OFF", "0", "false", "no"])
def test_managed_connections_deny_disabling_recursive_triggers(
    database: Database, disabled: str
) -> None:
    with database.engine.connect() as connection:
        with pytest.raises(DBAPIError, match="not authorized"):
            connection.exec_driver_sql(f"PRAGMA recursive_triggers={disabled}")
        assert connection.exec_driver_sql("PRAGMA recursive_triggers").scalar_one() == 1


@pytest.mark.parametrize(
    ("pragma", "unsafe_value"),
    [
        ("foreign_keys", "OFF"),
        ("foreign_keys", "0"),
        ("recursive_triggers", "OFF"),
        ("journal_mode", "DELETE"),
        ("journal_mode", "MEMORY"),
    ],
)
def test_managed_connections_deny_unsafe_pragma_changes(
    database: Database, pragma: str, unsafe_value: str
) -> None:
    with database.engine.connect() as connection:
        with pytest.raises(DBAPIError, match="not authorized"):
            connection.exec_driver_sql(f"PRAGMA {pragma}={unsafe_value}")

        expected = "wal" if pragma == "journal_mode" else 1
        actual = connection.exec_driver_sql(f"PRAGMA {pragma}").scalar_one()
        assert str(actual).casefold() == str(expected)


def test_checkout_restores_poisoned_pragmas_and_authorizer(database: Database) -> None:
    candidate_id, draft_id = _seed_candidate(database, "poisoned-checkout")
    with database.sessions.begin() as db_session:
        db_session.add(_confirmation("poisoned-checkout-token", candidate_id, draft_id))
    raw = database.engine.raw_connection()
    dbapi = raw.driver_connection
    assert dbapi is not None
    dbapi.set_authorizer(None)
    dbapi.execute("PRAGMA foreign_keys=OFF")
    dbapi.execute("PRAGMA recursive_triggers=OFF")
    assert dbapi.execute("PRAGMA journal_mode=DELETE").fetchone()[0] == "delete"
    raw.close()

    with database.engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        assert connection.exec_driver_sql("PRAGMA recursive_triggers").scalar_one() == 1
        assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "wal"
        with pytest.raises(DBAPIError, match="not authorized"):
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        with pytest.raises(DBAPIError, match="session id already exists"):
            connection.exec_driver_sql(
                "INSERT OR REPLACE INTO session "
                "(id, created_at, label, purge_after, nav_budget, nav_used, "
                "send_enabled) VALUES "
                "('session-poisoned-checkout', 'now', 'replacement', 'later', "
                "120, 0, 0)"
            )
        assert (
            connection.exec_driver_sql(
                "SELECT COUNT(*) FROM send_confirmation "
                "WHERE token='poisoned-checkout-token'"
            ).scalar_one()
            == 1
        )


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_rollback_journal_is_rejected_before_sqlite_without_mutation(
    tmp_path: Path, kind: str
) -> None:
    path = tmp_path / "journal-guard.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE sentinel (value TEXT NOT NULL)")
        connection.execute("INSERT INTO sentinel VALUES ('untouched')")
    os.chmod(path, 0o600)
    database_bytes = path.read_bytes()
    journal = Path(f"{path}-journal")
    target = tmp_path / "journal-target"
    target.write_bytes(b"journal-sentinel")
    os.chmod(target, 0o640)
    if kind == "symlink":
        journal.symlink_to(target)
    elif kind == "hardlink":
        os.link(target, journal)
    else:
        target.unlink()
        os.mkfifo(journal, 0o640)

    target_bytes = target.read_bytes() if target.exists() else None
    target_mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else None
    journal_mode = stat.S_IMODE(journal.lstat().st_mode)
    database = Database(path)
    try:
        with pytest.raises((OSError, ValueError)):
            database.initialize()
    finally:
        database.dispose()

    assert os.path.lexists(journal)
    assert stat.S_IMODE(journal.lstat().st_mode) == journal_mode
    if target.exists():
        assert target.read_bytes() == target_bytes
        assert stat.S_IMODE(target.stat().st_mode) == target_mode
    assert path.read_bytes() == database_bytes


@pytest.mark.parametrize(
    ("confirm_send", "state", "finished_at"),
    [
        (0, "SENDING", None),
        (0, "SENT", LATER),
        (0, "FAILED_CONCLUSIVE", LATER),
        (0, "AMBIGUOUS", LATER),
        (1, "DRY_RUN_OK", LATER),
        (1, "DRY_RUN_FAILED", LATER),
    ],
)
def test_confirm_send_state_family_is_enforced_on_insert(
    database: Database, confirm_send: int, state: str, finished_at: str | None
) -> None:
    candidate_id, draft_id = _seed_candidate(database, f"family-{state}-{confirm_send}")
    with pytest.raises(DBAPIError, match=r"confirm_send state family|CHECK constraint"):
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO send_attempt "
                    "(id, candidate_id, draft_id, idempotency_key, body_sha256, "
                    "confirm_send, state, started_at, finished_at, resolution) VALUES "
                    "(:id, :candidate, :draft, :key, :hash, :confirm, :state, "
                    ":started, :finished, 'unresolved')"
                ),
                {
                    "id": f"attempt-family-{state}-{confirm_send}",
                    "candidate": candidate_id,
                    "draft": draft_id,
                    "key": f"family-{state}-{confirm_send}".ljust(64, "0"),
                    "hash": "a" * 64,
                    "confirm": confirm_send,
                    "state": state,
                    "started": NOW,
                    "finished": finished_at,
                },
            )


@pytest.mark.parametrize(
    ("initial_state", "initial_confirm", "new_state", "new_confirm"),
    [
        ("SENDING", 1, "SENDING", 0),
        ("DRY_RUN_OK", 0, "DRY_RUN_OK", 1),
    ],
)
def test_confirm_send_state_family_is_enforced_on_update(
    database: Database,
    initial_state: str,
    initial_confirm: int,
    new_state: str,
    new_confirm: int,
) -> None:
    candidate_id, draft_id = _seed_candidate(database, f"family-update-{initial_state}")
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO send_attempt "
                "(id, candidate_id, draft_id, idempotency_key, body_sha256, "
                "confirm_send, state, started_at, finished_at, resolution) VALUES "
                "('attempt-family-update', :candidate, :draft, :key, :hash, "
                ":confirm, :state, :started, :finished, 'unresolved')"
            ),
            {
                "candidate": candidate_id,
                "draft": draft_id,
                "key": f"family-update-{initial_state}".ljust(64, "0"),
                "hash": "a" * 64,
                "confirm": initial_confirm,
                "state": initial_state,
                "started": NOW,
                "finished": None if initial_state == "SENDING" else LATER,
            },
        )

    with pytest.raises(DBAPIError, match=r"confirm_send state family|immutable"):
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE send_attempt SET state=:state, confirm_send=:confirm "
                    "WHERE id='attempt-family-update'"
                ),
                {"state": new_state, "confirm": new_confirm},
            )


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("token", "renamed-token"),
        ("candidate_id", "candidate-confirmation-target"),
        ("draft_id", "draft-confirmation-target"),
        ("body_sha256", "b" * 64),
        ("created_at", LATER),
        ("expires_at", NOW),
    ],
)
def test_confirmation_identity_and_expiry_are_immutable(
    database: Database, column: str, value: str
) -> None:
    candidate_id, draft_id = _seed_candidate(database, "confirmation-source")
    _seed_candidate(database, "confirmation-target")
    with database.sessions.begin() as db_session:
        db_session.add(_confirmation("confirmation-token", candidate_id, draft_id))

    with pytest.raises(DBAPIError, match="send_confirmation is immutable"):
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    f"UPDATE send_confirmation SET {column}=:value WHERE token=:token"
                ),
                {"value": value, "token": "confirmation-token"},
            )


def test_confirmation_consumption_is_one_way_and_exactly_once(
    database: Database,
) -> None:
    candidate_id, draft_id = _seed_candidate(database, "confirmation-consume")
    with database.sessions.begin() as db_session:
        db_session.add(_confirmation("consume-token", candidate_id, draft_id))

    with database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE send_confirmation SET consumed_at=:at "
                "WHERE token='consume-token'"
            ),
            {"at": NOW},
        )

    for value in (None, LATER):
        with pytest.raises(DBAPIError, match="consumed_at may be set exactly once"):
            with database.engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE send_confirmation SET consumed_at=:at "
                        "WHERE token='consume-token'"
                    ),
                    {"at": value},
                )


def test_new_confirmation_cannot_start_consumed(database: Database) -> None:
    candidate_id, draft_id = _seed_candidate(database, "confirmation-preconsumed")
    with pytest.raises(DBAPIError, match="must start unconsumed"):
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO send_confirmation "
                    "(token, candidate_id, draft_id, body_sha256, created_at, "
                    "expires_at, consumed_at) VALUES "
                    "('preconsumed-token', :candidate, :draft, :hash, :now, "
                    ":later, :now)"
                ),
                {
                    "candidate": candidate_id,
                    "draft": draft_id,
                    "hash": "a" * 64,
                    "now": NOW,
                    "later": LATER,
                },
            )


@pytest.mark.parametrize("record", ["confirmation", "attempt"])
@pytest.mark.parametrize("column", ["username", "profile_url", "profile_urn"])
def test_recipient_identity_freezes_at_approval_and_through_completed_history(
    database: Database, record: str, column: str
) -> None:
    candidate_id, draft_id = _seed_candidate(database, f"recipient-{record}-{column}")
    with database.sessions.begin() as db_session:
        if record == "confirmation":
            db_session.add(
                _confirmation(f"token-{record}-{column}", candidate_id, draft_id)
            )
        else:
            db_session.add(
                _attempt(
                    f"attempt-{record}-{column}",
                    candidate_id,
                    draft_id,
                    state="SENT",
                )
            )

    with pytest.raises(DBAPIError, match="recipient identity is immutable"):
        with database.engine.begin() as connection:
            connection.execute(
                text(f"UPDATE candidate SET {column}=:value WHERE id=:candidate"),
                {"value": f"changed-{column}", "candidate": candidate_id},
            )


def test_candidate_replace_cannot_retarget_approved_recipient_with_triggers_off(
    database: Database,
) -> None:
    candidate_id, draft_id = _seed_candidate(database, "candidate-replace")
    with database.sessions.begin() as db_session:
        db_session.add(_confirmation("candidate-replace-token", candidate_id, draft_id))
    path = database.path
    database.dispose()

    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA recursive_triggers=OFF")
        with pytest.raises(sqlite3.IntegrityError, match="recipient identity"):
            connection.execute(
                "INSERT OR REPLACE INTO candidate "
                "(id, session_id, username, profile_url, profile_urn, first_seen_at, "
                "stage, retrieval_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    candidate_id,
                    "session-candidate-replace",
                    "retargeted-person",
                    "https://www.linkedin.com/in/retargeted-person/",
                    "urn:li:fsd_profile:retargeted",
                    NOW,
                    "discovered",
                    "pending",
                ),
            )


@pytest.mark.parametrize("recursive_triggers", ["ON", "OFF"])
@pytest.mark.parametrize("conflict", ["id", "username"])
def test_candidate_update_replace_cannot_delete_approved_recipient(
    database: Database, recursive_triggers: str, conflict: str
) -> None:
    candidate_id, draft_id = _seed_candidate(
        database, f"candidate-update-{recursive_triggers}-{conflict}"
    )
    with database.sessions.begin() as db_session:
        db_session.add(
            _confirmation(f"candidate-update-{conflict}", candidate_id, draft_id)
        )
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO candidate "
                "(id, session_id, username, profile_url, first_seen_at, stage, "
                "retrieval_status) VALUES "
                "(:id, :session, :username, :url, :now, 'discovered', 'pending')"
            ),
            {
                "id": f"candidate-victim-{conflict}",
                "session": f"session-candidate-update-{recursive_triggers}-{conflict}",
                "username": f"victim-{conflict}",
                "url": f"https://www.linkedin.com/in/victim-{conflict}/",
                "now": NOW,
            },
        )
    path = database.path
    database.dispose()

    assignment = "id=?" if conflict == "id" else "username=?"
    value = (
        candidate_id
        if conflict == "id"
        else f"person-candidate-update-{recursive_triggers}-{conflict}"
    )
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA recursive_triggers={recursive_triggers}")
        with pytest.raises(sqlite3.IntegrityError, match="recipient identity"):
            connection.execute(
                f"UPDATE OR REPLACE candidate SET {assignment} WHERE id=?",
                (value, f"candidate-victim-{conflict}"),
            )
        assert connection.execute(
            "SELECT COUNT(*) FROM send_confirmation WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone() == (1,)


@pytest.mark.parametrize("operation", ["insert", "update", "delete"])
def test_draft_claims_are_immutable_after_approval(
    database: Database, operation: str
) -> None:
    candidate_id, draft_id = _seed_candidate(database, f"claim-{operation}")
    with database.sessions.begin() as db_session:
        db_session.add(
            DraftClaim(
                id="approved-claim",
                draft_id=draft_id,
                claim_text="Grounded claim",
                grounded=True,
            )
        )
        db_session.flush()
        db_session.add(
            _confirmation(f"claim-token-{operation}", candidate_id, draft_id)
        )

    statements = {
        "insert": (
            "INSERT INTO draft_claim (id, draft_id, claim_text, grounded) "
            "VALUES ('late-claim', :draft, 'Late claim', 1)"
        ),
        "update": (
            "UPDATE draft_claim SET claim_text='Changed' WHERE id='approved-claim'"
        ),
        "delete": "DELETE FROM draft_claim WHERE id='approved-claim'",
    }
    with pytest.raises(DBAPIError, match="approved draft_claim is immutable"):
        with database.engine.begin() as connection:
            connection.execute(text(statements[operation]), {"draft": draft_id})


def test_referenced_draft_replace_is_blocked_with_recursive_triggers_off(
    database: Database,
) -> None:
    candidate_id, draft_id = _seed_candidate(database, "draft-replace")
    with database.sessions.begin() as db_session:
        db_session.add(_confirmation("draft-replace-token", candidate_id, draft_id))
    path = database.path
    database.dispose()

    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA recursive_triggers=OFF")
        with pytest.raises(
            sqlite3.IntegrityError, match="message_draft already exists"
        ):
            connection.execute(
                "INSERT OR REPLACE INTO message_draft "
                "(id, candidate_id, version, body, body_sha256, char_count, generator, "
                "grounding_status, grounding_report, created_at) VALUES "
                "(?, ?, 1, 'Retargeted', ?, 10, 'manual', 'pass', '{}', ?)",
                (draft_id, candidate_id, "b" * 64, NOW),
            )


@pytest.mark.parametrize("recursive_triggers", ["ON", "OFF"])
@pytest.mark.parametrize("conflict", ["id", "version"])
def test_draft_update_replace_cannot_delete_referenced_draft(
    database: Database, recursive_triggers: str, conflict: str
) -> None:
    suffix = f"draft-update-{recursive_triggers}-{conflict}"
    candidate_id, draft_id = _seed_candidate(database, suffix)
    with database.sessions.begin() as db_session:
        db_session.add(
            _confirmation(f"draft-update-{conflict}", candidate_id, draft_id)
        )
        db_session.add(
            MessageDraft(
                id=f"draft-victim-{conflict}",
                candidate_id=candidate_id,
                version=2,
                body="Victim",
                body_sha256="b" * 64,
                char_count=6,
                generator="manual",
                grounding_status="pass",
                grounding_report={},
                created_at=NOW,
            )
        )
    path = database.path
    database.dispose()

    assignment = "id=?" if conflict == "id" else "version=?"
    value: str | int = draft_id if conflict == "id" else 1
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA recursive_triggers={recursive_triggers}")
        with pytest.raises(
            sqlite3.IntegrityError, match="message_draft already exists"
        ):
            connection.execute(
                f"UPDATE OR REPLACE message_draft SET {assignment} WHERE id=?",
                (value, f"draft-victim-{conflict}"),
            )
        assert connection.execute(
            "SELECT COUNT(*) FROM send_confirmation WHERE draft_id=?", (draft_id,)
        ).fetchone() == (1,)


@pytest.mark.parametrize("recursive_triggers", ["ON", "OFF"])
@pytest.mark.parametrize("operation", ["insert", "update"])
def test_session_replace_cannot_delete_existing_history(
    database: Database, recursive_triggers: str, operation: str
) -> None:
    candidate_id, draft_id = _seed_candidate(
        database, f"session-replace-{recursive_triggers}-{operation}"
    )
    session_id = f"session-session-replace-{recursive_triggers}-{operation}"
    with database.sessions.begin() as db_session:
        db_session.add(
            _confirmation(f"session-replace-{operation}", candidate_id, draft_id)
        )
        if operation == "update":
            db_session.add(
                DashboardSession(
                    id="session-replace-victim",
                    created_at=NOW,
                    label="Victim",
                    purge_after=LATER,
                    nav_budget=120,
                    nav_used=0,
                    send_enabled=False,
                )
            )
    path = database.path
    database.dispose()

    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA recursive_triggers={recursive_triggers}")
        with pytest.raises(sqlite3.IntegrityError, match="session id already exists"):
            if operation == "insert":
                connection.execute(
                    "INSERT OR REPLACE INTO session "
                    "(id, created_at, label, purge_after, nav_budget, nav_used, "
                    "send_enabled) VALUES (?, ?, 'Replacement', ?, 120, 0, 0)",
                    (session_id, NOW, LATER),
                )
            else:
                connection.execute(
                    "UPDATE OR REPLACE session SET id=? "
                    "WHERE id='session-replace-victim'",
                    (session_id,),
                )
        assert connection.execute(
            "SELECT COUNT(*) FROM send_confirmation WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone() == (1,)


def test_linked_evidence_is_immutable_after_approval(database: Database) -> None:
    candidate_id, draft_id = _seed_candidate(database, "evidence-freeze")
    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO role_brief "
            "(id, session_id, version, created_at, job_description, target_titles, "
            "location, industries, positive_keywords, negative_keywords, message_tone, "
            "weights_version) VALUES "
            "('brief-evidence', 'session-evidence-freeze', 1, 'now', 'job', '[]', "
            "'anywhere', '[]', '[]', '[]', 'plain', 'v1')"
        )
        connection.exec_driver_sql(
            "INSERT INTO score "
            "(id, candidate_id, brief_id, weights_version, stage, score, score_lower, "
            "score_upper, confidence, confidence_band, computed_at, is_current) VALUES "
            "('score-evidence', 'candidate-evidence-freeze', 'brief-evidence', 'v1', "
            "'provisional', 1, 1, 1, 1, 'high', 'now', 1)"
        )
        connection.exec_driver_sql(
            "INSERT INTO score_signal "
            "(id, score_id, signal_id, weight, verdict, raw_subscore, contribution, "
            "availability) VALUES "
            "('signal-evidence', 'score-evidence', 'skill', 1, 'matched', 1, 1, 1)"
        )
        connection.execute(
            insert(Evidence),
            {
                "id": "evidence-approved",
                "score_signal_id": "signal-evidence",
                "section_name": "experience",
                "span_start": 0,
                "span_end": 5,
                "snippet": "Python",
                "matcher": "exact",
                "matched_term": "Python",
                "polarity": "supporting",
            },
        )
        connection.execute(
            insert(DraftClaim),
            {
                "id": "claim-with-evidence",
                "draft_id": draft_id,
                "claim_text": "Python experience",
                "evidence_id": "evidence-approved",
                "grounded": True,
            },
        )
        connection.execute(
            insert(SendConfirmation),
            {
                "token": "evidence-token",
                "candidate_id": candidate_id,
                "draft_id": draft_id,
                "body_sha256": "a" * 64,
                "created_at": NOW,
                "expires_at": LATER,
            },
        )

    for statement in (
        "UPDATE evidence SET snippet='Changed' WHERE id='evidence-approved'",
        "DELETE FROM evidence WHERE id='evidence-approved'",
    ):
        with pytest.raises(DBAPIError, match="approved evidence is immutable"):
            with database.engine.begin() as connection:
                connection.exec_driver_sql(statement)


@pytest.mark.parametrize("approval_record", ["confirmation", "sent_attempt"])
def test_approved_evidence_supports_only_one_way_raw_purge(
    database: Database, approval_record: str
) -> None:
    suffix = f"raw-purge-{approval_record}"
    candidate_id, draft_id = _seed_candidate(database, suffix)
    raw_snippet = f"private profile snippet {approval_record}"
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
            "INSERT INTO parsed_field "
            "(id, candidate_id, field_key, value, section_name, span_start, span_end, "
            "snippet, origin, parser_version, created_at) VALUES "
            f"('parsed-{suffix}', 'candidate-{suffix}', 'skill', "
            f"'{raw_snippet}', 'experience', 0, 20, '{raw_snippet}', "
            "'deterministic', 'v1', 'now')"
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
                "(id, score_signal_id, parsed_field_id, section_name, span_start, "
                "span_end, snippet, matcher, matched_term, polarity) VALUES "
                "(:evidence, :signal, :parsed, 'experience', 0, 20, :snippet, "
                "'exact', :snippet, 'supporting')"
            ),
            {
                "evidence": f"evidence-{suffix}",
                "signal": f"signal-{suffix}",
                "parsed": f"parsed-{suffix}",
                "snippet": raw_snippet,
            },
        )
        connection.execute(
            text(
                "INSERT INTO draft_claim "
                "(id, draft_id, claim_text, evidence_id, grounded) VALUES "
                "(:claim, :draft, 'Grounded claim', :evidence, 1)"
            ),
            {
                "claim": f"claim-{suffix}",
                "draft": draft_id,
                "evidence": f"evidence-{suffix}",
            },
        )
        if approval_record == "confirmation":
            connection.execute(
                insert(SendConfirmation),
                {
                    "token": f"token-{suffix}",
                    "candidate_id": candidate_id,
                    "draft_id": draft_id,
                    "body_sha256": "a" * 64,
                    "created_at": NOW,
                    "expires_at": LATER,
                },
            )
        else:
            connection.execute(
                insert(SendAttempt),
                {
                    "id": f"attempt-{suffix}",
                    "candidate_id": candidate_id,
                    "draft_id": draft_id,
                    "idempotency_key": f"attempt-{suffix}".ljust(64, "0"),
                    "body_sha256": "a" * 64,
                    "confirm_send": True,
                    "state": "SENT",
                    "started_at": NOW,
                    "finished_at": LATER,
                    "resolution": "unresolved",
                },
            )

    with database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE evidence SET snippet=:sentinel, matched_term=:sentinel, "
                "parsed_field_id=NULL, purged_at=:purged_at WHERE id=:evidence"
            ),
            {
                "sentinel": v0009_integrity_completion.PURGED_EVIDENCE_SENTINEL,
                "purged_at": LATER,
                "evidence": f"evidence-{suffix}",
            },
        )
        connection.execute(
            text("DELETE FROM parsed_field WHERE id=:parsed"),
            {"parsed": f"parsed-{suffix}"},
        )

    with database.engine.connect() as connection:
        evidence_row = connection.execute(
            text(
                "SELECT snippet, matched_term, parsed_field_id, purged_at "
                "FROM evidence WHERE id=:evidence"
            ),
            {"evidence": f"evidence-{suffix}"},
        ).one()
        claim_evidence = connection.execute(
            text("SELECT evidence_id FROM draft_claim WHERE id=:claim"),
            {"claim": f"claim-{suffix}"},
        ).scalar_one()
        stored_raw = connection.execute(
            text(
                "SELECT COUNT(*) FROM parsed_field "
                "WHERE value LIKE :raw OR snippet LIKE :raw"
            ),
            {"raw": f"%{raw_snippet}%"},
        ).scalar_one()

    assert tuple(evidence_row) == (
        v0009_integrity_completion.PURGED_EVIDENCE_SENTINEL,
        v0009_integrity_completion.PURGED_EVIDENCE_SENTINEL,
        None,
        LATER,
    )
    assert claim_evidence == f"evidence-{suffix}"
    assert stored_raw == 0

    for statement in (
        "UPDATE evidence SET purged_at=NULL WHERE id=:evidence",
        "UPDATE evidence SET snippet='restored' WHERE id=:evidence",
        "DELETE FROM evidence WHERE id=:evidence",
    ):
        with pytest.raises(DBAPIError, match=r"purged evidence|approved evidence"):
            with database.engine.begin() as connection:
                connection.execute(text(statement), {"evidence": f"evidence-{suffix}"})


def test_full_session_purge_preserves_all_new_history_guards(
    database: Database,
) -> None:
    candidate_id, draft_id = _seed_candidate(database, "guarded-purge")
    with database.sessions.begin() as db_session:
        db_session.add(
            DraftClaim(
                id="purged-claim",
                draft_id=draft_id,
                claim_text="Approved claim",
                grounded=True,
            )
        )
        db_session.flush()
        db_session.add(_confirmation("purged-token", candidate_id, draft_id))

    with database.engine.begin() as connection:
        connection.execute(text("DELETE FROM session WHERE id='session-guarded-purge'"))
        counts = {
            table: connection.exec_driver_sql(
                f"SELECT COUNT(*) FROM {table}"
            ).scalar_one()
            for table in (
                "candidate",
                "message_draft",
                "draft_claim",
                "send_confirmation",
            )
        }

    assert counts == {
        "candidate": 0,
        "message_draft": 0,
        "draft_claim": 0,
        "send_confirmation": 0,
    }


def test_confirmation_replace_collision_survives_recursive_triggers_off(
    database: Database,
) -> None:
    candidate_id, draft_id = _seed_candidate(database, "confirmation-replace")
    with database.sessions.begin() as db_session:
        db_session.add(_confirmation("replace-token", candidate_id, draft_id))
    path = database.path
    database.dispose()

    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA recursive_triggers=OFF")
        with pytest.raises(sqlite3.IntegrityError, match="token already exists"):
            connection.execute(
                "INSERT OR REPLACE INTO send_confirmation "
                "(token, candidate_id, draft_id, body_sha256, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("replace-token", candidate_id, draft_id, "a" * 64, NOW, LATER),
            )


def test_audit_replace_collision_survives_recursive_triggers_off(
    database: Database,
) -> None:
    _seed_candidate(database, "audit-replace")
    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO audit_log "
            "(id, session_id, at, actor, action, subject_type, subject_id, detail, "
            "correlation_id) VALUES "
            "('audit-existing', 'session-audit-replace', 'now', 'system', "
            "'created', 'session', 'session-audit-replace', '{}', 'correlation')"
        )
    path = database.path
    database.dispose()

    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA recursive_triggers=OFF")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "INSERT OR REPLACE INTO audit_log "
                "(id, session_id, at, actor, action, subject_type, subject_id, detail, "
                "correlation_id) VALUES "
                "('audit-existing', 'session-audit-replace', 'later', 'system', "
                "'replaced', 'session', 'session-audit-replace', '{}', 'replacement')"
            )


@pytest.mark.parametrize("conflict", ["primary", "idempotency"])
def test_attempt_replace_collisions_survive_recursive_triggers_off(
    database: Database, conflict: str
) -> None:
    candidate_id, draft_id = _seed_candidate(database, f"attempt-replace-{conflict}")
    existing = _attempt(
        f"existing-{conflict}",
        candidate_id,
        draft_id,
        state="FAILED_CONCLUSIVE",
    )
    with database.sessions.begin() as db_session:
        db_session.add(existing)
    path = database.path
    database.dispose()
    new_id = existing.id if conflict == "primary" else f"new-{existing.id}"
    new_key = (
        "unique-primary-key".ljust(64, "0")
        if conflict == "primary"
        else existing.idempotency_key
    )
    expected_error = (
        "send_attempt id already exists"
        if conflict == "primary"
        else "send_attempt idempotency key already exists"
    )

    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA recursive_triggers=OFF")
        with pytest.raises(sqlite3.IntegrityError, match=expected_error):
            connection.execute(
                "INSERT OR REPLACE INTO send_attempt "
                "(id, candidate_id, draft_id, idempotency_key, body_sha256, "
                "confirm_send, state, started_at, finished_at, resolution) VALUES "
                "(?, ?, ?, ?, ?, 1, 'FAILED_CONCLUSIVE', ?, ?, 'unresolved')",
                (
                    new_id,
                    candidate_id,
                    draft_id,
                    new_key,
                    "a" * 64,
                    NOW,
                    LATER,
                ),
            )


def test_v0008_preflight_rejects_legacy_confirm_state_mismatch(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "legacy-confirm-state.db")
    database.initialize()
    candidate_id, draft_id = _seed_candidate(database, "legacy-family")
    with database.engine.begin() as connection:
        connection.execute(
            text("DELETE FROM schema_migration WHERE version=:version"),
            {"version": v0008_history_hardening.VERSION},
        )
        connection.exec_driver_sql("DROP TRIGGER send_attempt_confirm_state_insert")
        connection.exec_driver_sql("PRAGMA ignore_check_constraints=ON")
        connection.execute(
            text(
                "INSERT INTO send_attempt "
                "(id, candidate_id, draft_id, idempotency_key, body_sha256, "
                "confirm_send, state, started_at, finished_at, resolution) VALUES "
                "('legacy-family-attempt', :candidate, :draft, :key, :hash, 1, "
                "'DRY_RUN_OK', :now, :later, 'unresolved')"
            ),
            {
                "candidate": candidate_id,
                "draft": draft_id,
                "key": "legacy-family".ljust(64, "0"),
                "hash": "a" * 64,
                "now": NOW,
                "later": LATER,
            },
        )
        connection.exec_driver_sql("PRAGMA ignore_check_constraints=OFF")
    database.dispose()
    database = Database(database.path)

    try:
        with pytest.raises(
            RuntimeError, match="incompatible confirm_send state family"
        ):
            database.initialize()
    finally:
        database.dispose()

    with sqlite3.connect(database.path) as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM schema_migration WHERE version=?",
                (v0008_history_hardening.VERSION,),
            ).fetchone()
            is None
        )
        assert connection.execute(
            "SELECT state, confirm_send FROM send_attempt "
            "WHERE id='legacy-family-attempt'"
        ).fetchone() == ("DRY_RUN_OK", 1)


@pytest.mark.parametrize(
    "failure_after", range(1, len(v0008_history_hardening.STATEMENTS) + 1)
)
def test_v0008_each_statement_is_atomic_and_retryable(
    tmp_path: Path, monkeypatch, failure_after: int
) -> None:
    database = Database(tmp_path / f"interrupted-v8-{failure_after}.db")
    database.initialize()
    with database.engine.begin() as connection:
        connection.execute(
            text("DELETE FROM schema_migration WHERE version=:version"),
            {"version": v0008_history_hardening.VERSION},
        )
        for trigger_name in (
            "audit_log_insert_collision",
            "send_attempt_insert_id_collision",
            "send_attempt_insert_idempotency_collision",
            "send_attempt_insert_live_collision",
            "send_confirmation_insert_collision",
            "send_confirmation_is_immutable",
            "send_attempt_confirm_state_insert",
            "send_attempt_confirm_state_update",
            "candidate_recipient_identity_is_immutable",
            "candidate_send_reference_insert_collision",
            "candidate_with_send_reference_no_direct_delete",
            "referenced_message_draft_insert_collision",
            "approved_draft_claim_insert",
            "approved_draft_claim_update",
            "approved_draft_claim_delete",
            "approved_evidence_insert_collision",
            "approved_evidence_update",
            "approved_evidence_delete",
        ):
            connection.exec_driver_sql(f'DROP TRIGGER "{trigger_name}"')
        connection.exec_driver_sql(
            "CREATE TRIGGER candidate_with_attempt_no_direct_delete "
            "BEFORE DELETE ON candidate FOR EACH ROW "
            "WHEN EXISTS (SELECT 1 FROM send_attempt WHERE candidate_id = OLD.id) "
            "AND EXISTS (SELECT 1 FROM session WHERE id = OLD.session_id) "
            "BEGIN SELECT RAISE(ABORT, 'candidate with send history may be deleted "
            "only by full-session purge'); END"
        )
    baseline = _schema_objects(database.path)
    database.dispose()
    database = Database(database.path)
    original_apply = v0008_history_hardening.apply

    def interrupted_apply(connection) -> None:
        for index, statement in enumerate(v0008_history_hardening.STATEMENTS, start=1):
            connection.exec_driver_sql(statement)
            if index == failure_after:
                raise RuntimeError(f"interrupted after statement {index}")

    monkeypatch.setattr(v0008_history_hardening, "apply", interrupted_apply)
    with pytest.raises(RuntimeError, match=f"statement {failure_after}"):
        database.initialize()

    assert _schema_objects(database.path) == baseline
    monkeypatch.setattr(v0008_history_hardening, "apply", original_apply)
    database.initialize()
    database.dispose()


_V0009_TRIGGER_NAMES = (
    "session_insert_id_collision",
    "session_update_id_collision",
    "candidate_update_send_reference_collision",
    "referenced_message_draft_update_collision",
    "send_confirmation_insert_unconsumed",
    "evidence_purge_state_insert",
    "evidence_purge_state_update",
    "purged_evidence_is_immutable",
    "approved_evidence_update",
)


def _prepare_pre_v0009_schema(database: Database, *, drop_column: bool) -> None:
    with database.engine.begin() as connection:
        connection.execute(
            text("DELETE FROM schema_migration WHERE version=:version"),
            {"version": v0012_score_session_provenance.VERSION},
        )
        for trigger_name in v0012_score_session_provenance.TRIGGER_NAMES:
            connection.exec_driver_sql(f'DROP TRIGGER "{trigger_name}"')
        connection.execute(
            text("DELETE FROM schema_migration WHERE version=:version"),
            {"version": v0011_purged_evidence_ancestry.VERSION},
        )
        for trigger_name in v0011_purged_evidence_ancestry.TRIGGER_NAMES:
            connection.exec_driver_sql(f'DROP TRIGGER "{trigger_name}"')
        connection.execute(
            text("DELETE FROM schema_migration WHERE version=:version"),
            {"version": v0010_takeover_guards.VERSION},
        )
        for trigger_name in (
            "purged_evidence_insert_collision",
            "purged_evidence_update_collision",
            "purged_evidence_no_direct_delete",
            "referenced_draft_claim_update_collision",
        ):
            connection.exec_driver_sql(f'DROP TRIGGER "{trigger_name}"')
        connection.execute(
            text("DELETE FROM schema_migration WHERE version=:version"),
            {"version": v0009_integrity_completion.VERSION},
        )
        for trigger_name in _V0009_TRIGGER_NAMES:
            connection.exec_driver_sql(f'DROP TRIGGER "{trigger_name}"')
        approved_update = next(
            statement
            for statement in v0008_history_hardening.STATEMENTS
            if "CREATE TRIGGER approved_evidence_update" in statement
        )
        connection.exec_driver_sql(approved_update)
        if drop_column:
            connection.exec_driver_sql("ALTER TABLE evidence DROP COLUMN purged_at")


def test_v0009_upgrade_accepts_historical_consumed_confirmation(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "historical-consumed.db")
    database.initialize()
    candidate_id, draft_id = _seed_candidate(database, "historical-consumed")
    with database.sessions.begin() as db_session:
        db_session.add(
            _confirmation("historical-consumed-token", candidate_id, draft_id)
        )
    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE send_confirmation SET consumed_at='2026-09-02T12:00:00+00:00' "
            "WHERE token='historical-consumed-token'"
        )
    _prepare_pre_v0009_schema(database, drop_column=True)
    database.dispose()

    upgraded = Database(database.path)
    try:
        upgraded.initialize()
        with upgraded.engine.connect() as connection:
            assert (
                connection.exec_driver_sql(
                    "SELECT consumed_at FROM send_confirmation "
                    "WHERE token='historical-consumed-token'"
                ).scalar_one()
                == NOW
            )
            columns = {
                row[1]
                for row in connection.exec_driver_sql(
                    "PRAGMA table_info(evidence)"
                ).fetchall()
            }
            assert "purged_at" in columns
    finally:
        upgraded.dispose()


@pytest.mark.parametrize(
    "failure_after", range(1, len(v0009_integrity_completion.STATEMENTS) + 1)
)
def test_v0009_each_trigger_statement_is_atomic_and_retryable(
    tmp_path: Path, monkeypatch, failure_after: int
) -> None:
    database = Database(tmp_path / f"interrupted-v9-{failure_after}.db")
    database.initialize()
    _prepare_pre_v0009_schema(database, drop_column=False)
    baseline = _schema_objects(database.path)
    database.dispose()
    database = Database(database.path)
    original_apply = v0009_integrity_completion.apply

    def interrupted_apply(connection) -> None:
        for index, statement in enumerate(
            v0009_integrity_completion.STATEMENTS, start=1
        ):
            connection.exec_driver_sql(statement)
            if index == failure_after:
                raise RuntimeError(f"interrupted after v9 statement {index}")

    monkeypatch.setattr(v0009_integrity_completion, "apply", interrupted_apply)
    with pytest.raises(RuntimeError, match=f"v9 statement {failure_after}"):
        database.initialize()

    assert _schema_objects(database.path) == baseline
    monkeypatch.setattr(v0009_integrity_completion, "apply", original_apply)
    database.initialize()
    database.dispose()


def test_v0009_column_addition_is_atomic_and_retryable(
    tmp_path: Path, monkeypatch
) -> None:
    database = Database(tmp_path / "interrupted-v9-column.db")
    database.initialize()
    _prepare_pre_v0009_schema(database, drop_column=True)
    database.dispose()
    database = Database(database.path)
    original_apply = v0009_integrity_completion.apply

    def interrupted_apply(connection) -> None:
        v0009_integrity_completion._ensure_purged_at_column(connection)
        raise RuntimeError("interrupted after v9 column")

    monkeypatch.setattr(v0009_integrity_completion, "apply", interrupted_apply)
    with pytest.raises(RuntimeError, match="v9 column"):
        database.initialize()
    with sqlite3.connect(database.path) as connection:
        assert "purged_at" not in {
            row[1] for row in connection.execute("PRAGMA table_info(evidence)")
        }

    monkeypatch.setattr(v0009_integrity_completion, "apply", original_apply)
    database.initialize()
    database.dispose()
