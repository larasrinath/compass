from __future__ import annotations

from sqlalchemy import Connection

VERSION = "0006_send_state_timing"

_INVALID_STATE_TIMING = """
       (state = 'SENDING' AND finished_at IS NOT NULL)
    OR (state <> 'SENDING' AND finished_at IS NULL)
"""

STATEMENTS = (
    "DROP TRIGGER IF EXISTS send_attempt_state_timing_insert",
    "DROP TRIGGER IF EXISTS send_attempt_state_timing_update",
    """
    CREATE TRIGGER send_attempt_state_timing_insert
    BEFORE INSERT ON send_attempt
    FOR EACH ROW
    WHEN (NEW.state = 'SENDING' AND NEW.finished_at IS NOT NULL)
      OR (NEW.state <> 'SENDING' AND NEW.finished_at IS NULL)
    BEGIN
      SELECT RAISE(
        ABORT,
        'send_attempt SENDING state must be unfinished and every outcome finished'
      );
    END
    """,
    """
    CREATE TRIGGER send_attempt_state_timing_update
    BEFORE UPDATE ON send_attempt
    FOR EACH ROW
    WHEN (OLD.state = 'SENDING' AND OLD.finished_at IS NOT NULL)
      OR (OLD.state <> 'SENDING' AND OLD.finished_at IS NULL)
      OR (NEW.state = 'SENDING' AND NEW.finished_at IS NOT NULL)
      OR (NEW.state <> 'SENDING' AND NEW.finished_at IS NULL)
    BEGIN
      SELECT RAISE(
        ABORT,
        'send_attempt SENDING state must be unfinished and every outcome finished'
      );
    END
    """,
)


def apply(connection: Connection) -> None:
    if connection.exec_driver_sql(
        f"SELECT EXISTS (SELECT 1 FROM send_attempt WHERE {_INVALID_STATE_TIMING})"
    ).scalar_one():
        raise RuntimeError(f"cannot apply {VERSION}: incompatible send state timing")
    for statement in STATEMENTS:
        connection.exec_driver_sql(statement)
