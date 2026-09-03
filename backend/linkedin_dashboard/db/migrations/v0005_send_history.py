from __future__ import annotations

from sqlalchemy import Connection

VERSION = "0005_send_history"

_IDENTITY_COLUMNS = (
    "id",
    "candidate_id",
    "draft_id",
    "idempotency_key",
    "body_sha256",
    "confirm_send",
    "started_at",
)
_IDENTITY_CHANGE = " OR ".join(
    f"NEW.{column} IS NOT OLD.{column}" for column in _IDENTITY_COLUMNS
)

STATEMENTS = (
    "DROP TRIGGER IF EXISTS send_attempt_identity_is_immutable",
    "DROP TRIGGER IF EXISTS send_attempt_no_direct_delete",
    "DROP TRIGGER IF EXISTS candidate_with_attempt_no_direct_delete",
    "DROP TRIGGER IF EXISTS draft_with_attempt_no_direct_delete",
    f"""
    CREATE TRIGGER send_attempt_identity_is_immutable
    BEFORE UPDATE ON send_attempt
    FOR EACH ROW
    WHEN {_IDENTITY_CHANGE}
    BEGIN
      SELECT RAISE(
        ABORT,
        'send_attempt identity and provenance are immutable at all times'
      );
    END
    """,
    """
    CREATE TRIGGER send_attempt_no_direct_delete
    BEFORE DELETE ON send_attempt
    FOR EACH ROW
    WHEN EXISTS (
      SELECT 1
        FROM candidate
        JOIN session ON session.id = candidate.session_id
       WHERE candidate.id = OLD.candidate_id
    )
    BEGIN
      SELECT RAISE(
        ABORT,
        'send_attempt history may be deleted only by full-session purge'
      );
    END
    """,
    """
    CREATE TRIGGER candidate_with_attempt_no_direct_delete
    BEFORE DELETE ON candidate
    FOR EACH ROW
    WHEN EXISTS (SELECT 1 FROM send_attempt WHERE candidate_id = OLD.id)
     AND EXISTS (SELECT 1 FROM session WHERE id = OLD.session_id)
    BEGIN
      SELECT RAISE(
        ABORT,
        'candidate with send history may be deleted only by full-session purge'
      );
    END
    """,
    """
    CREATE TRIGGER draft_with_attempt_no_direct_delete
    BEFORE DELETE ON message_draft
    FOR EACH ROW
    WHEN EXISTS (SELECT 1 FROM send_attempt WHERE draft_id = OLD.id)
     AND EXISTS (
       SELECT 1
         FROM candidate
         JOIN session ON session.id = candidate.session_id
        WHERE candidate.id = OLD.candidate_id
     )
    BEGIN
      SELECT RAISE(
        ABORT,
        'draft with send history may be deleted only by full-session purge'
      );
    END
    """,
)


def apply(connection: Connection) -> None:
    for statement in STATEMENTS:
        connection.exec_driver_sql(statement)
