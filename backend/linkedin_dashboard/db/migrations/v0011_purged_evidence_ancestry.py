from __future__ import annotations

from sqlalchemy import Connection

VERSION = "0011_purged_evidence_ancestry"

TRIGGER_NAMES = (
    "purged_score_signal_insert_collision",
    "purged_score_signal_update_guard",
    "purged_score_signal_delete_guard",
    "purged_score_insert_collision",
    "purged_score_update_guard",
    "purged_score_delete_guard",
    "purged_candidate_insert_collision",
    "purged_candidate_update_guard",
    "purged_candidate_delete_guard",
    "purged_role_brief_insert_collision",
    "purged_role_brief_update_guard",
    "purged_role_brief_delete_guard",
)


def _signal_has_live_tombstone(alias: str) -> str:
    return f"""
    EXISTS (
      SELECT 1
        FROM evidence AS evidence_row
        JOIN score AS parent_score ON parent_score.id = {alias}.score_id
        JOIN candidate AS parent_candidate
          ON parent_candidate.id = parent_score.candidate_id
        JOIN session AS live_session
          ON live_session.id = parent_candidate.session_id
       WHERE evidence_row.score_signal_id = {alias}.id
         AND evidence_row.purged_at IS NOT NULL
    )
    """


def _score_has_live_tombstone(alias: str) -> str:
    return f"""
    EXISTS (
      SELECT 1
        FROM score_signal AS child_signal
        JOIN evidence AS evidence_row
          ON evidence_row.score_signal_id = child_signal.id
        JOIN candidate AS parent_candidate
          ON parent_candidate.id = {alias}.candidate_id
        JOIN session AS live_session
          ON live_session.id = parent_candidate.session_id
       WHERE child_signal.score_id = {alias}.id
         AND evidence_row.purged_at IS NOT NULL
    )
    """


def _candidate_has_live_tombstone(alias: str) -> str:
    return f"""
    EXISTS (
      SELECT 1
        FROM score AS child_score
        JOIN score_signal AS child_signal
          ON child_signal.score_id = child_score.id
        JOIN evidence AS evidence_row
          ON evidence_row.score_signal_id = child_signal.id
        JOIN session AS live_session ON live_session.id = {alias}.session_id
       WHERE child_score.candidate_id = {alias}.id
         AND evidence_row.purged_at IS NOT NULL
    )
    """


def _brief_has_live_tombstone(alias: str) -> str:
    return f"""
    EXISTS (
      SELECT 1
        FROM score AS child_score
        JOIN score_signal AS child_signal
          ON child_signal.score_id = child_score.id
        JOIN evidence AS evidence_row
          ON evidence_row.score_signal_id = child_signal.id
        JOIN session AS live_session ON live_session.id = {alias}.session_id
       WHERE child_score.brief_id = {alias}.id
         AND evidence_row.purged_at IS NOT NULL
    )
    """


STATEMENTS = (
    f"""
    CREATE TRIGGER purged_score_signal_insert_collision
    BEFORE INSERT ON score_signal
    FOR EACH ROW
    WHEN EXISTS (
      SELECT 1 FROM score_signal AS protected
       WHERE protected.id = NEW.id
         AND {_signal_has_live_tombstone("protected")}
    )
    BEGIN
      SELECT RAISE(ABORT, 'purged evidence ancestor already exists');
    END
    """,
    f"""
    CREATE TRIGGER purged_score_signal_update_guard
    BEFORE UPDATE ON score_signal
    FOR EACH ROW
    WHEN {_signal_has_live_tombstone("OLD")}
      OR EXISTS (
        SELECT 1 FROM score_signal AS protected
         WHERE protected.id = NEW.id
           AND protected.id IS NOT OLD.id
           AND {_signal_has_live_tombstone("protected")}
      )
    BEGIN
      SELECT RAISE(ABORT, 'purged evidence ancestor is immutable');
    END
    """,
    f"""
    CREATE TRIGGER purged_score_signal_delete_guard
    BEFORE DELETE ON score_signal
    FOR EACH ROW
    WHEN {_signal_has_live_tombstone("OLD")}
    BEGIN
      SELECT RAISE(ABORT, 'purged evidence ancestor delete is forbidden');
    END
    """,
    f"""
    CREATE TRIGGER purged_score_insert_collision
    BEFORE INSERT ON score
    FOR EACH ROW
    WHEN EXISTS (
      SELECT 1 FROM score AS protected
       WHERE protected.id = NEW.id
         AND {_score_has_live_tombstone("protected")}
    )
    BEGIN
      SELECT RAISE(ABORT, 'purged evidence ancestor already exists');
    END
    """,
    f"""
    CREATE TRIGGER purged_score_update_guard
    BEFORE UPDATE ON score
    FOR EACH ROW
    WHEN {_score_has_live_tombstone("OLD")}
      OR EXISTS (
        SELECT 1 FROM score AS protected
         WHERE protected.id = NEW.id
           AND protected.id IS NOT OLD.id
           AND {_score_has_live_tombstone("protected")}
      )
    BEGIN
      SELECT RAISE(ABORT, 'purged evidence ancestor is immutable');
    END
    """,
    f"""
    CREATE TRIGGER purged_score_delete_guard
    BEFORE DELETE ON score
    FOR EACH ROW
    WHEN {_score_has_live_tombstone("OLD")}
    BEGIN
      SELECT RAISE(ABORT, 'purged evidence ancestor delete is forbidden');
    END
    """,
    f"""
    CREATE TRIGGER purged_candidate_insert_collision
    BEFORE INSERT ON candidate
    FOR EACH ROW
    WHEN EXISTS (
      SELECT 1 FROM candidate AS protected
       WHERE (
              protected.id = NEW.id
           OR (protected.session_id = NEW.session_id
               AND protected.username = NEW.username)
       )
         AND {_candidate_has_live_tombstone("protected")}
    )
    BEGIN
      SELECT RAISE(ABORT, 'purged evidence candidate identity already exists');
    END
    """,
    f"""
    CREATE TRIGGER purged_candidate_update_guard
    BEFORE UPDATE ON candidate
    FOR EACH ROW
    WHEN {_candidate_has_live_tombstone("OLD")}
      OR EXISTS (
        SELECT 1 FROM candidate AS protected
         WHERE protected.id IS NOT OLD.id
           AND (
                  protected.id = NEW.id
               OR (protected.session_id = NEW.session_id
                   AND protected.username = NEW.username)
           )
           AND {_candidate_has_live_tombstone("protected")}
      )
    BEGIN
      SELECT RAISE(ABORT, 'purged evidence candidate identity is immutable');
    END
    """,
    f"""
    CREATE TRIGGER purged_candidate_delete_guard
    BEFORE DELETE ON candidate
    FOR EACH ROW
    WHEN {_candidate_has_live_tombstone("OLD")}
    BEGIN
      SELECT RAISE(ABORT, 'purged evidence candidate delete is forbidden');
    END
    """,
    f"""
    CREATE TRIGGER purged_role_brief_insert_collision
    BEFORE INSERT ON role_brief
    FOR EACH ROW
    WHEN EXISTS (
      SELECT 1 FROM role_brief AS protected
       WHERE (
              protected.id = NEW.id
           OR (protected.session_id = NEW.session_id
               AND protected.version = NEW.version)
       )
         AND {_brief_has_live_tombstone("protected")}
    )
    BEGIN
      SELECT RAISE(ABORT, 'purged evidence role brief identity already exists');
    END
    """,
    f"""
    CREATE TRIGGER purged_role_brief_update_guard
    BEFORE UPDATE ON role_brief
    FOR EACH ROW
    WHEN {_brief_has_live_tombstone("OLD")}
      OR EXISTS (
        SELECT 1 FROM role_brief AS protected
         WHERE protected.id IS NOT OLD.id
           AND (
                  protected.id = NEW.id
               OR (protected.session_id = NEW.session_id
                   AND protected.version = NEW.version)
           )
           AND {_brief_has_live_tombstone("protected")}
      )
    BEGIN
      SELECT RAISE(ABORT, 'purged evidence role brief identity is immutable');
    END
    """,
    f"""
    CREATE TRIGGER purged_role_brief_delete_guard
    BEFORE DELETE ON role_brief
    FOR EACH ROW
    WHEN {_brief_has_live_tombstone("OLD")}
    BEGIN
      SELECT RAISE(ABORT, 'purged evidence role brief delete is forbidden');
    END
    """,
)


def apply(connection: Connection) -> None:
    for statement in STATEMENTS:
        connection.exec_driver_sql(statement)
