from __future__ import annotations

from sqlalchemy import Connection

VERSION = "0001_constraints"

STATEMENTS = (
    """
    CREATE UNIQUE INDEX IF NOT EXISTS one_live_send_per_candidate
      ON send_attempt(candidate_id)
      WHERE confirm_send = 1
        AND (
              state IN ('SENDING', 'SENT')
           OR (state = 'AMBIGUOUS'
               AND resolution IN ('unresolved', 'confirmed_sent'))
            )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS send_attempt_is_immutable
    BEFORE UPDATE ON send_attempt
    FOR EACH ROW
    WHEN OLD.finished_at IS NOT NULL
     AND (   NEW.state           IS NOT OLD.state
          OR NEW.idempotency_key IS NOT OLD.idempotency_key
          OR NEW.body_sha256     IS NOT OLD.body_sha256
          OR NEW.confirm_send    IS NOT OLD.confirm_send
          OR NEW.draft_id        IS NOT OLD.draft_id
          OR NEW.tool_status     IS NOT OLD.tool_status
          OR NEW.tool_sent       IS NOT OLD.tool_sent
          OR NEW.tool_recipient_selected IS NOT OLD.tool_recipient_selected
          OR NEW.tool_url        IS NOT OLD.tool_url
          OR NEW.raw_response    IS NOT OLD.raw_response
          OR NEW.error_class     IS NOT OLD.error_class
          OR NEW.error_message   IS NOT OLD.error_message
          OR NEW.started_at      IS NOT OLD.started_at
          OR NEW.finished_at     IS NOT OLD.finished_at)
    BEGIN
      SELECT RAISE(
        ABORT,
        'send_attempt is immutable once finished; only resolution fields may change'
      );
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS send_resolution_is_final
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
    """
    CREATE TRIGGER IF NOT EXISTS audit_log_no_update
    BEFORE UPDATE ON audit_log
    FOR EACH ROW
    BEGIN
      SELECT RAISE(ABORT, 'audit_log is append-only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
    BEFORE DELETE ON audit_log
    FOR EACH ROW
    BEGIN
      SELECT RAISE(ABORT, 'audit_log is append-only');
    END
    """,
)


def apply(connection: Connection) -> None:
    for statement in STATEMENTS:
        connection.exec_driver_sql(statement)
