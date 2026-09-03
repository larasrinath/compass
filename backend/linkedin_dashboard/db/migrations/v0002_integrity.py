from __future__ import annotations

from sqlalchemy import Connection

VERSION = "0002_integrity"

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


def _boolean_trigger(
    table: str, columns: tuple[tuple[str, bool], ...], operation: str
) -> str:
    invalid = []
    for column, nullable in columns:
        expression = f"NEW.{column} NOT IN (0, 1)"
        if nullable:
            expression = f"(NEW.{column} IS NOT NULL AND {expression})"
        invalid.append(expression)
    return f"""
    CREATE TRIGGER validate_{table}_booleans_{operation.casefold()}
    BEFORE {operation} ON {table}
    FOR EACH ROW
    WHEN {" OR ".join(invalid)}
    BEGIN
      SELECT RAISE(ABORT, '{table} contains an invalid boolean value');
    END
    """


STATEMENTS = (
    "DROP TRIGGER IF EXISTS send_attempt_is_immutable",
    "DROP TRIGGER IF EXISTS send_resolution_is_final",
    f"""
    CREATE TRIGGER send_attempt_is_immutable
    BEFORE UPDATE ON send_attempt
    FOR EACH ROW
    WHEN OLD.finished_at IS NOT NULL
     AND ({_IMMUTABLE_CHANGE})
    BEGIN
      SELECT RAISE(
        ABORT,
        'send_attempt is immutable once finished; only resolution fields may change'
      );
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
           NEW.resolution IN ('confirmed_sent', 'confirmed_not_sent')
       AND NEW.resolved_at IS NOT NULL
     )
    BEGIN
      SELECT RAISE(
        ABORT,
        'send_attempt resolution requires one complete transition from unresolved'
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
    _boolean_trigger("session", (("send_enabled", False),), "INSERT"),
    _boolean_trigger("session", (("send_enabled", False),), "UPDATE"),
    _boolean_trigger("score", (("is_current", False),), "INSERT"),
    _boolean_trigger("score", (("is_current", False),), "UPDATE"),
    _boolean_trigger("draft_claim", (("grounded", False),), "INSERT"),
    _boolean_trigger("draft_claim", (("grounded", False),), "UPDATE"),
    _boolean_trigger(
        "send_attempt",
        (
            ("confirm_send", False),
            ("tool_sent", True),
            ("tool_recipient_selected", True),
        ),
        "INSERT",
    ),
    _boolean_trigger(
        "send_attempt",
        (
            ("confirm_send", False),
            ("tool_sent", True),
            ("tool_recipient_selected", True),
        ),
        "UPDATE",
    ),
)


def apply(connection: Connection) -> None:
    connection.exec_driver_sql(
        "DROP TRIGGER IF EXISTS send_resolution_transition_is_valid"
    )
    for statement in STATEMENTS:
        connection.exec_driver_sql(statement)
