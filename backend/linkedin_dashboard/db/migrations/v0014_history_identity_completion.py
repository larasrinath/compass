from __future__ import annotations

from sqlalchemy import Connection

VERSION = "0014_history_identity_completion"

TRIGGER_NAMES = (
    "score_insert_identity_collision",
    "score_identity_is_immutable",
)

STATEMENTS = (
    """
    CREATE TRIGGER score_insert_identity_collision
    BEFORE INSERT ON score
    FOR EACH ROW
    WHEN EXISTS (SELECT 1 FROM score WHERE score.id = NEW.id)
    BEGIN
      SELECT RAISE(ABORT, 'score identity already exists and is immutable');
    END
    """,
    """
    CREATE TRIGGER score_identity_is_immutable
    BEFORE UPDATE OF id ON score
    FOR EACH ROW
    WHEN NEW.id IS NOT OLD.id
    BEGIN
      SELECT RAISE(ABORT, 'score identity is immutable');
    END
    """,
)


def apply(connection: Connection) -> None:
    for statement in STATEMENTS:
        connection.exec_driver_sql(statement)
