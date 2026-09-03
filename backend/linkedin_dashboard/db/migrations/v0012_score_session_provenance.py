from __future__ import annotations

from sqlalchemy import Connection

VERSION = "0012_score_session_provenance"

TRIGGER_NAMES = (
    "score_requires_same_session_insert",
    "score_requires_same_session_update",
    "candidate_preserves_score_session",
    "role_brief_preserves_score_session",
)

_NEW_PAIR_HAS_SAME_SESSION = """
EXISTS (
  SELECT 1
    FROM candidate AS score_candidate
    JOIN role_brief AS score_brief
      ON score_brief.id = NEW.brief_id
     AND score_brief.session_id = score_candidate.session_id
   WHERE score_candidate.id = NEW.candidate_id
)
"""

STATEMENTS = (
    f"""
    CREATE TRIGGER score_requires_same_session_insert
    BEFORE INSERT ON score
    FOR EACH ROW
    WHEN NOT ({_NEW_PAIR_HAS_SAME_SESSION})
    BEGIN
      SELECT RAISE(ABORT, 'score candidate and role brief must share a session');
    END
    """,
    f"""
    CREATE TRIGGER score_requires_same_session_update
    BEFORE UPDATE ON score
    FOR EACH ROW
    WHEN NOT ({_NEW_PAIR_HAS_SAME_SESSION})
    BEGIN
      SELECT RAISE(ABORT, 'score candidate and role brief must share a session');
    END
    """,
    """
    CREATE TRIGGER candidate_preserves_score_session
    BEFORE UPDATE OF session_id ON candidate
    FOR EACH ROW
    WHEN EXISTS (
      SELECT 1
        FROM score AS existing_score
        JOIN role_brief AS score_brief
          ON score_brief.id = existing_score.brief_id
       WHERE existing_score.candidate_id = OLD.id
         AND score_brief.session_id IS NOT NEW.session_id
    )
    BEGIN
      SELECT RAISE(ABORT, 'candidate update would cross a score session');
    END
    """,
    """
    CREATE TRIGGER role_brief_preserves_score_session
    BEFORE UPDATE OF session_id ON role_brief
    FOR EACH ROW
    WHEN EXISTS (
      SELECT 1
        FROM score AS existing_score
        JOIN candidate AS score_candidate
          ON score_candidate.id = existing_score.candidate_id
       WHERE existing_score.brief_id = OLD.id
         AND score_candidate.session_id IS NOT NEW.session_id
    )
    BEGIN
      SELECT RAISE(ABORT, 'role brief update would cross a score session');
    END
    """,
)


def _preflight(connection: Connection) -> None:
    violation = connection.exec_driver_sql(
        """
        SELECT EXISTS (
          SELECT 1
            FROM score AS existing_score
            LEFT JOIN candidate AS score_candidate
              ON score_candidate.id = existing_score.candidate_id
            LEFT JOIN role_brief AS score_brief
              ON score_brief.id = existing_score.brief_id
           WHERE score_candidate.id IS NULL
              OR score_brief.id IS NULL
              OR score_candidate.session_id IS NOT score_brief.session_id
        )
        """
    ).scalar_one()
    if violation:
        raise RuntimeError(
            f"cannot apply {VERSION}: score candidate and role brief sessions differ"
        )


def apply(connection: Connection) -> None:
    _preflight(connection)
    for statement in STATEMENTS:
        connection.exec_driver_sql(statement)
