from __future__ import annotations

from sqlalchemy import Connection

VERSION = "0007_send_provenance"

_DRAFT_COLUMNS = (
    "id",
    "candidate_id",
    "version",
    "body",
    "body_sha256",
    "char_count",
    "generator",
    "grounding_status",
    "grounding_report",
    "created_at",
)
_DRAFT_CHANGE = " OR ".join(
    f"NEW.{column} IS NOT OLD.{column}" for column in _DRAFT_COLUMNS
)

_PREFLIGHT = """
SELECT EXISTS (
    SELECT 1
      FROM send_confirmation AS confirmation
      LEFT JOIN message_draft AS draft ON draft.id = confirmation.draft_id
     WHERE draft.id IS NULL
        OR confirmation.candidate_id IS NOT draft.candidate_id
        OR confirmation.body_sha256 IS NOT draft.body_sha256
    UNION ALL
    SELECT 1
      FROM send_attempt AS attempt
      LEFT JOIN message_draft AS draft ON draft.id = attempt.draft_id
     WHERE draft.id IS NULL
        OR attempt.candidate_id IS NOT draft.candidate_id
        OR attempt.body_sha256 IS NOT draft.body_sha256
)
"""

STATEMENTS = (
    "DROP TRIGGER IF EXISTS send_confirmation_provenance_insert",
    "DROP TRIGGER IF EXISTS send_confirmation_provenance_update",
    "DROP TRIGGER IF EXISTS send_attempt_provenance_insert",
    "DROP TRIGGER IF EXISTS send_attempt_provenance_update",
    "DROP TRIGGER IF EXISTS referenced_message_draft_is_immutable",
    "DROP TRIGGER IF EXISTS draft_with_attempt_no_direct_delete",
    "DROP TRIGGER IF EXISTS draft_with_send_reference_no_direct_delete",
    """
    CREATE TRIGGER send_confirmation_provenance_insert
    BEFORE INSERT ON send_confirmation
    FOR EACH ROW
    WHEN NOT EXISTS (
      SELECT 1 FROM message_draft AS draft
       WHERE draft.id = NEW.draft_id
         AND draft.candidate_id = NEW.candidate_id
         AND draft.body_sha256 = NEW.body_sha256
    )
    BEGIN
      SELECT RAISE(ABORT, 'send_confirmation must match its approved draft');
    END
    """,
    """
    CREATE TRIGGER send_confirmation_provenance_update
    BEFORE UPDATE ON send_confirmation
    FOR EACH ROW
    WHEN NOT EXISTS (
      SELECT 1 FROM message_draft AS draft
       WHERE draft.id = NEW.draft_id
         AND draft.candidate_id = NEW.candidate_id
         AND draft.body_sha256 = NEW.body_sha256
    )
    BEGIN
      SELECT RAISE(ABORT, 'send_confirmation must match its approved draft');
    END
    """,
    """
    CREATE TRIGGER send_attempt_provenance_insert
    BEFORE INSERT ON send_attempt
    FOR EACH ROW
    WHEN NOT EXISTS (
      SELECT 1 FROM message_draft AS draft
       WHERE draft.id = NEW.draft_id
         AND draft.candidate_id = NEW.candidate_id
         AND draft.body_sha256 = NEW.body_sha256
    )
    BEGIN
      SELECT RAISE(ABORT, 'send_attempt must match its approved draft');
    END
    """,
    """
    CREATE TRIGGER send_attempt_provenance_update
    BEFORE UPDATE ON send_attempt
    FOR EACH ROW
    WHEN NOT EXISTS (
      SELECT 1 FROM message_draft AS draft
       WHERE draft.id = NEW.draft_id
         AND draft.candidate_id = NEW.candidate_id
         AND draft.body_sha256 = NEW.body_sha256
    )
    BEGIN
      SELECT RAISE(ABORT, 'send_attempt must match its approved draft');
    END
    """,
    f"""
    CREATE TRIGGER referenced_message_draft_is_immutable
    BEFORE UPDATE ON message_draft
    FOR EACH ROW
    WHEN (
         EXISTS (SELECT 1 FROM send_confirmation WHERE draft_id = OLD.id)
      OR EXISTS (SELECT 1 FROM send_attempt WHERE draft_id = OLD.id)
    )
    AND ({_DRAFT_CHANGE})
    BEGIN
      SELECT RAISE(
        ABORT,
        'referenced message_draft is append-only; create a new draft version'
      );
    END
    """,
    """
    CREATE TRIGGER draft_with_send_reference_no_direct_delete
    BEFORE DELETE ON message_draft
    FOR EACH ROW
    WHEN (
         EXISTS (SELECT 1 FROM send_confirmation WHERE draft_id = OLD.id)
      OR EXISTS (SELECT 1 FROM send_attempt WHERE draft_id = OLD.id)
    )
    AND EXISTS (
      SELECT 1
        FROM candidate
        JOIN session ON session.id = candidate.session_id
       WHERE candidate.id = OLD.candidate_id
    )
    BEGIN
      SELECT RAISE(
        ABORT,
        'referenced draft may be deleted only by full-session purge'
      );
    END
    """,
)


def apply(connection: Connection) -> None:
    if connection.exec_driver_sql(_PREFLIGHT).scalar_one():
        raise RuntimeError(f"cannot apply {VERSION}: incompatible send provenance")
    for statement in STATEMENTS:
        connection.exec_driver_sql(statement)
