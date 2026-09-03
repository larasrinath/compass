from __future__ import annotations

from sqlalchemy import Connection

VERSION = "0013_history_root_immutability"

TRIGGER_NAMES = (
    "candidate_history_session_is_immutable",
    "score_root_insert_collision",
    "score_roots_are_immutable",
)

STATEMENTS = (
    """
    CREATE TRIGGER candidate_history_session_is_immutable
    BEFORE UPDATE OF session_id ON candidate
    FOR EACH ROW
    WHEN NEW.session_id IS NOT OLD.session_id
     AND (
       EXISTS (
         SELECT 1 FROM send_confirmation
          WHERE send_confirmation.candidate_id = OLD.id
       )
       OR EXISTS (
         SELECT 1 FROM send_attempt
          WHERE send_attempt.candidate_id = OLD.id
       )
     )
    BEGIN
      SELECT RAISE(ABORT, 'candidate session is immutable after send approval');
    END
    """,
    """
    CREATE TRIGGER score_root_insert_collision
    BEFORE INSERT ON score
    FOR EACH ROW
    WHEN EXISTS (
      SELECT 1 FROM score AS existing_score
       WHERE existing_score.id = NEW.id
         AND (
              existing_score.candidate_id IS NOT NEW.candidate_id
           OR existing_score.brief_id IS NOT NEW.brief_id
         )
    )
    BEGIN
      SELECT RAISE(ABORT, 'score candidate and brief roots are immutable');
    END
    """,
    """
    CREATE TRIGGER score_roots_are_immutable
    BEFORE UPDATE OF candidate_id, brief_id ON score
    FOR EACH ROW
    WHEN NEW.candidate_id IS NOT OLD.candidate_id
      OR NEW.brief_id IS NOT OLD.brief_id
    BEGIN
      SELECT RAISE(ABORT, 'score candidate and brief roots are immutable');
    END
    """,
)


def apply(connection: Connection) -> None:
    for statement in STATEMENTS:
        connection.exec_driver_sql(statement)
