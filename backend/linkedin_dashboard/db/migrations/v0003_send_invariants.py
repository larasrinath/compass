from __future__ import annotations

from sqlalchemy import Connection

from linkedin_dashboard.db.migrations.v0002_integrity import preflight_integrity

VERSION = "0003_send_invariants"

_PROTECTED_SEND_COLUMNS = (
    "id",
    "candidate_id",
    "draft_id",
    "idempotency_key",
    "body_sha256",
    "confirm_send",
    "state",
    "tool_status",
    "tool_sent",
    "tool_recipient_selected",
    "tool_url",
    "raw_response",
    "error_class",
    "error_message",
    "started_at",
    "finished_at",
)
_IMMUTABLE_CHANGE = " OR ".join(
    f"NEW.{column} IS NOT OLD.{column}" for column in _PROTECTED_SEND_COLUMNS
)

STATEMENTS = (
    "DROP TRIGGER IF EXISTS send_attempt_insert_is_valid",
    "DROP TRIGGER IF EXISTS send_attempt_is_immutable",
    "DROP TRIGGER IF EXISTS send_attempt_update_is_valid",
    "DROP TRIGGER IF EXISTS send_resolution_transition_is_valid",
    "DROP TRIGGER IF EXISTS send_resolution_is_final",
    """
    CREATE TRIGGER send_attempt_insert_is_valid
    BEFORE INSERT ON send_attempt
    FOR EACH ROW
    WHEN NEW.resolution <> 'unresolved'
      OR NEW.resolved_at IS NOT NULL
      OR NEW.resolution_note IS NOT NULL
      OR (NEW.state = 'AMBIGUOUS' AND NEW.finished_at IS NULL)
    BEGIN
      SELECT RAISE(
        ABORT,
        'send_attempt must start unresolved; AMBIGUOUS must already be finished'
      );
    END
    """,
    f"""
    CREATE TRIGGER send_attempt_is_immutable
    BEFORE UPDATE ON send_attempt
    FOR EACH ROW
    WHEN (OLD.finished_at IS NOT NULL OR OLD.state = 'AMBIGUOUS')
     AND ({_IMMUTABLE_CHANGE})
    BEGIN
      SELECT RAISE(
        ABORT,
        'finished or AMBIGUOUS send_attempt identity and provenance are immutable'
      );
    END
    """,
    """
    CREATE TRIGGER send_attempt_update_is_valid
    BEFORE UPDATE ON send_attempt
    FOR EACH ROW
    WHEN (NEW.state = 'AMBIGUOUS' AND NEW.finished_at IS NULL)
      OR (NEW.resolution = 'unresolved'
          AND (NEW.resolved_at IS NOT NULL OR NEW.resolution_note IS NOT NULL))
      OR (NEW.resolution <> 'unresolved'
          AND (NEW.state <> 'AMBIGUOUS'
               OR NEW.finished_at IS NULL
               OR NEW.resolved_at IS NULL))
    BEGIN
      SELECT RAISE(ABORT, 'send_attempt update violates resolution invariants');
    END
    """,
    """
    CREATE TRIGGER send_resolution_transition_is_valid
    BEFORE UPDATE ON send_attempt
    FOR EACH ROW
    WHEN OLD.resolution = 'unresolved'
     AND (   NEW.resolution      IS NOT OLD.resolution
          OR NEW.resolved_at     IS NOT OLD.resolved_at
          OR NEW.resolution_note IS NOT OLD.resolution_note)
     AND NOT (
           OLD.state = 'AMBIGUOUS'
       AND OLD.finished_at IS NOT NULL
       AND OLD.resolution = 'unresolved'
       AND OLD.resolved_at IS NULL
       AND OLD.resolution_note IS NULL
       AND NEW.resolution IN ('confirmed_sent', 'confirmed_not_sent')
       AND NEW.resolved_at IS NOT NULL
     )
    BEGIN
      SELECT RAISE(
        ABORT,
        'send_attempt resolution requires one finished AMBIGUOUS transition'
      );
    END
    """,
    """
    CREATE TRIGGER send_resolution_is_final
    BEFORE UPDATE ON send_attempt
    FOR EACH ROW
    WHEN OLD.resolution <> 'unresolved'
     AND (   NEW.resolution      IS NOT OLD.resolution
          OR NEW.resolved_at     IS NOT OLD.resolved_at
          OR NEW.resolution_note IS NOT OLD.resolution_note)
    BEGIN
      SELECT RAISE(
        ABORT,
        'send_attempt.resolution is already set and cannot be changed'
      );
    END
    """,
)


def apply(connection: Connection) -> None:
    preflight_integrity(connection, version=VERSION)
    for statement in STATEMENTS:
        connection.exec_driver_sql(statement)
