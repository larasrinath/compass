from __future__ import annotations

from sqlalchemy import Connection

VERSION = "0009_integrity_completion"
PURGED_EVIDENCE_SENTINEL = "[purged]"

_DRAFT_REFERENCED = """
     EXISTS (SELECT 1 FROM send_confirmation WHERE draft_id = {draft_id})
  OR EXISTS (SELECT 1 FROM send_attempt WHERE draft_id = {draft_id})
"""

_EVIDENCE_APPROVED = """
EXISTS (
  SELECT 1
    FROM draft_claim AS claim
   WHERE claim.evidence_id = {evidence_id}
     AND (
          EXISTS (SELECT 1 FROM send_confirmation WHERE draft_id = claim.draft_id)
       OR EXISTS (SELECT 1 FROM send_attempt WHERE draft_id = claim.draft_id)
     )
)
"""

_VALID_PURGE_STATE = """
({prefix}purged_at IS NULL)
OR (
     {prefix}purged_at IS NOT NULL
 AND {prefix}snippet = '{sentinel}'
 AND {prefix}matched_term = '{sentinel}'
 AND {prefix}parsed_field_id IS NULL
)
"""
_NEW_VALID_PURGE_STATE = _VALID_PURGE_STATE.format(
    prefix="NEW.", sentinel=PURGED_EVIDENCE_SENTINEL
)

_APPROVED_PURGE_TRANSITION = f"""
    OLD.purged_at IS NULL
AND NEW.purged_at IS NOT NULL
AND NEW.snippet = '{PURGED_EVIDENCE_SENTINEL}'
AND NEW.matched_term = '{PURGED_EVIDENCE_SENTINEL}'
AND NEW.parsed_field_id IS NULL
AND NEW.id IS OLD.id
AND NEW.score_signal_id IS OLD.score_signal_id
AND NEW.section_name IS OLD.section_name
AND NEW.span_start IS OLD.span_start
AND NEW.span_end IS OLD.span_end
AND NEW.matcher IS OLD.matcher
AND NEW.polarity IS OLD.polarity
"""

STATEMENTS = (
    "DROP TRIGGER IF EXISTS session_insert_id_collision",
    "DROP TRIGGER IF EXISTS session_update_id_collision",
    "DROP TRIGGER IF EXISTS candidate_update_send_reference_collision",
    "DROP TRIGGER IF EXISTS referenced_message_draft_update_collision",
    "DROP TRIGGER IF EXISTS send_confirmation_insert_unconsumed",
    "DROP TRIGGER IF EXISTS evidence_purge_state_insert",
    "DROP TRIGGER IF EXISTS evidence_purge_state_update",
    "DROP TRIGGER IF EXISTS purged_evidence_is_immutable",
    "DROP TRIGGER IF EXISTS approved_evidence_update",
    """
    CREATE TRIGGER session_insert_id_collision
    BEFORE INSERT ON session
    FOR EACH ROW
    WHEN EXISTS (SELECT 1 FROM session WHERE id = NEW.id)
    BEGIN
      SELECT RAISE(ABORT, 'session id already exists');
    END
    """,
    """
    CREATE TRIGGER session_update_id_collision
    BEFORE UPDATE ON session
    FOR EACH ROW
    WHEN EXISTS (
      SELECT 1 FROM session AS existing
       WHERE existing.id = NEW.id AND existing.id IS NOT OLD.id
    )
    BEGIN
      SELECT RAISE(ABORT, 'session id already exists');
    END
    """,
    """
    CREATE TRIGGER candidate_update_send_reference_collision
    BEFORE UPDATE ON candidate
    FOR EACH ROW
    WHEN EXISTS (
      SELECT 1
        FROM candidate AS existing
       WHERE existing.id IS NOT OLD.id
         AND (
              existing.id = NEW.id
           OR (existing.session_id = NEW.session_id
               AND existing.username = NEW.username)
         )
         AND (
              EXISTS (
                SELECT 1 FROM send_confirmation
                 WHERE candidate_id = existing.id
              )
           OR EXISTS (
                SELECT 1 FROM send_attempt WHERE candidate_id = existing.id
              )
         )
    )
    BEGIN
      SELECT RAISE(ABORT, 'approved candidate recipient identity already exists');
    END
    """,
    f"""
    CREATE TRIGGER referenced_message_draft_update_collision
    BEFORE UPDATE ON message_draft
    FOR EACH ROW
    WHEN EXISTS (
      SELECT 1
        FROM message_draft AS existing
       WHERE existing.id IS NOT OLD.id
         AND (
              existing.id = NEW.id
           OR (existing.candidate_id = NEW.candidate_id
               AND existing.version = NEW.version)
         )
         AND ({_DRAFT_REFERENCED.format(draft_id="existing.id")})
    )
    BEGIN
      SELECT RAISE(ABORT, 'referenced message_draft already exists');
    END
    """,
    """
    CREATE TRIGGER send_confirmation_insert_unconsumed
    BEFORE INSERT ON send_confirmation
    FOR EACH ROW
    WHEN NEW.consumed_at IS NOT NULL
    BEGIN
      SELECT RAISE(ABORT, 'send_confirmation must start unconsumed');
    END
    """,
    f"""
    CREATE TRIGGER evidence_purge_state_insert
    BEFORE INSERT ON evidence
    FOR EACH ROW
    WHEN NOT ({_NEW_VALID_PURGE_STATE})
    BEGIN
      SELECT RAISE(ABORT, 'evidence purge state is invalid');
    END
    """,
    f"""
    CREATE TRIGGER evidence_purge_state_update
    BEFORE UPDATE ON evidence
    FOR EACH ROW
    WHEN NOT ({_NEW_VALID_PURGE_STATE})
    BEGIN
      SELECT RAISE(ABORT, 'evidence purge state is invalid');
    END
    """,
    """
    CREATE TRIGGER purged_evidence_is_immutable
    BEFORE UPDATE ON evidence
    FOR EACH ROW
    WHEN OLD.purged_at IS NOT NULL
    BEGIN
      SELECT RAISE(ABORT, 'purged evidence is immutable');
    END
    """,
    f"""
    CREATE TRIGGER approved_evidence_update
    BEFORE UPDATE ON evidence
    FOR EACH ROW
    WHEN {_EVIDENCE_APPROVED.format(evidence_id="OLD.id")}
     AND NOT ({_APPROVED_PURGE_TRANSITION})
    BEGIN
      SELECT RAISE(ABORT, 'approved evidence is immutable');
    END
    """,
)


def _ensure_purged_at_column(connection: Connection) -> None:
    columns = {
        str(row[1])
        for row in connection.exec_driver_sql("PRAGMA table_info(evidence)").fetchall()
    }
    if "purged_at" not in columns:
        connection.exec_driver_sql("ALTER TABLE evidence ADD COLUMN purged_at TEXT")


def apply(connection: Connection) -> None:
    _ensure_purged_at_column(connection)
    if connection.exec_driver_sql(
        "SELECT EXISTS (SELECT 1 FROM evidence WHERE NOT ("
        + _VALID_PURGE_STATE.format(
            prefix="evidence.", sentinel=PURGED_EVIDENCE_SENTINEL
        )
        + "))"
    ).scalar_one():
        raise RuntimeError(f"cannot apply {VERSION}: incompatible evidence purge state")
    for statement in STATEMENTS:
        connection.exec_driver_sql(statement)
