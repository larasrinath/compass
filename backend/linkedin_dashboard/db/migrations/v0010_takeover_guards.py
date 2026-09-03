from __future__ import annotations

from sqlalchemy import Connection

VERSION = "0010_takeover_guards"

_DRAFT_REFERENCED = """
     EXISTS (SELECT 1 FROM send_confirmation WHERE draft_id = {draft_id})
  OR EXISTS (SELECT 1 FROM send_attempt WHERE draft_id = {draft_id})
"""

_EVIDENCE_HAS_LIVE_SESSION = """
EXISTS (
  SELECT 1
    FROM score_signal AS signal
    JOIN score ON score.id = signal.score_id
    JOIN candidate ON candidate.id = score.candidate_id
    JOIN session ON session.id = candidate.session_id
   WHERE signal.id = {score_signal_id}
)
"""

STATEMENTS = (
    "DROP TRIGGER IF EXISTS purged_evidence_insert_collision",
    "DROP TRIGGER IF EXISTS purged_evidence_update_collision",
    "DROP TRIGGER IF EXISTS purged_evidence_no_direct_delete",
    "DROP TRIGGER IF EXISTS referenced_draft_claim_update_collision",
    """
    CREATE TRIGGER purged_evidence_insert_collision
    BEFORE INSERT ON evidence
    FOR EACH ROW
    WHEN EXISTS (
      SELECT 1 FROM evidence AS existing
       WHERE existing.id = NEW.id AND existing.purged_at IS NOT NULL
    )
    BEGIN
      SELECT RAISE(ABORT, 'purged evidence id already exists');
    END
    """,
    """
    CREATE TRIGGER purged_evidence_update_collision
    BEFORE UPDATE ON evidence
    FOR EACH ROW
    WHEN EXISTS (
      SELECT 1 FROM evidence AS existing
       WHERE existing.id = NEW.id
         AND existing.id IS NOT OLD.id
         AND existing.purged_at IS NOT NULL
    )
    BEGIN
      SELECT RAISE(ABORT, 'purged evidence id already exists');
    END
    """,
    f"""
    CREATE TRIGGER purged_evidence_no_direct_delete
    BEFORE DELETE ON evidence
    FOR EACH ROW
    WHEN OLD.purged_at IS NOT NULL
     AND {_EVIDENCE_HAS_LIVE_SESSION.format(score_signal_id="OLD.score_signal_id")}
    BEGIN
      SELECT RAISE(ABORT, 'purged evidence may be deleted only by full-session purge');
    END
    """,
    f"""
    CREATE TRIGGER referenced_draft_claim_update_collision
    BEFORE UPDATE ON draft_claim
    FOR EACH ROW
    WHEN EXISTS (
      SELECT 1
        FROM draft_claim AS existing
       WHERE existing.id = NEW.id
         AND existing.id IS NOT OLD.id
         AND ({_DRAFT_REFERENCED.format(draft_id="existing.draft_id")})
    )
    BEGIN
      SELECT RAISE(ABORT, 'referenced draft_claim already exists');
    END
    """,
)


def apply(connection: Connection) -> None:
    for statement in STATEMENTS:
        connection.exec_driver_sql(statement)
