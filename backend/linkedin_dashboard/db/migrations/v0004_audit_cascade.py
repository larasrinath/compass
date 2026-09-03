from __future__ import annotations

from sqlalchemy import Connection

VERSION = "0004_audit_cascade"

STATEMENTS = (
    "DROP TRIGGER IF EXISTS audit_log_no_delete",
    """
    CREATE TRIGGER audit_log_no_delete
    BEFORE DELETE ON audit_log
    FOR EACH ROW
    WHEN EXISTS (SELECT 1 FROM session WHERE id = OLD.session_id)
    BEGIN
      SELECT RAISE(
        ABORT,
        'audit_log is append-only while its session exists; purge the session'
      );
    END
    """,
)


def apply(connection: Connection) -> None:
    for statement in STATEMENTS:
        connection.exec_driver_sql(statement)
