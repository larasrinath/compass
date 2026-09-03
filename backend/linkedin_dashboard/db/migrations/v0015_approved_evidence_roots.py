from __future__ import annotations

from sqlalchemy import Connection

VERSION = "0015_approved_evidence_roots"

TRIGGER_NAMES = (
    "draft_claim_evidence_candidate_insert",
    "draft_claim_evidence_candidate_update",
    "send_confirmation_claim_candidate_insert",
    "approved_score_signal_insert_collision",
    "approved_score_signal_update",
    "approved_score_signal_delete",
)

_CLAIM_CANDIDATE_MISMATCH = """
EXISTS (
  SELECT 1
    FROM message_draft AS draft
    JOIN evidence ON evidence.id = {evidence_id}
    JOIN score_signal ON score_signal.id = evidence.score_signal_id
    JOIN score ON score.id = score_signal.score_id
   WHERE draft.id = {draft_id}
     AND draft.candidate_id IS NOT score.candidate_id
)
"""

_SIGNAL_HAS_APPROVED_EVIDENCE = """
EXISTS (
  SELECT 1
    FROM evidence
    JOIN draft_claim AS claim ON claim.evidence_id = evidence.id
   WHERE evidence.score_signal_id = {signal_id}
     AND (
          EXISTS (
            SELECT 1 FROM send_confirmation
             WHERE send_confirmation.draft_id = claim.draft_id
          )
       OR EXISTS (
            SELECT 1 FROM send_attempt
             WHERE send_attempt.draft_id = claim.draft_id
          )
     )
)
"""

_EXISTING_CLAIM_MISMATCH = _CLAIM_CANDIDATE_MISMATCH.format(
    evidence_id="claim.evidence_id", draft_id="claim.draft_id"
)
_NEW_CLAIM_MISMATCH = _CLAIM_CANDIDATE_MISMATCH.format(
    evidence_id="NEW.evidence_id", draft_id="NEW.draft_id"
)
_CONFIRMATION_CLAIM_MISMATCH = """
EXISTS (
  SELECT 1
    FROM draft_claim AS claim
    JOIN evidence ON evidence.id = claim.evidence_id
    JOIN score_signal ON score_signal.id = evidence.score_signal_id
    JOIN score ON score.id = score_signal.score_id
   WHERE claim.draft_id = NEW.draft_id
     AND score.candidate_id IS NOT NEW.candidate_id
)
"""
_OLD_SIGNAL_APPROVED = _SIGNAL_HAS_APPROVED_EVIDENCE.format(signal_id="OLD.id")
_NEW_SIGNAL_APPROVED = _SIGNAL_HAS_APPROVED_EVIDENCE.format(signal_id="NEW.id")

_PREFLIGHT = f"""
SELECT EXISTS (
  SELECT 1
    FROM draft_claim AS claim
   WHERE claim.evidence_id IS NOT NULL
     AND ({_EXISTING_CLAIM_MISMATCH})
)
"""

STATEMENTS = (
    f"""
    CREATE TRIGGER draft_claim_evidence_candidate_insert
    BEFORE INSERT ON draft_claim
    FOR EACH ROW
    WHEN NEW.evidence_id IS NOT NULL
     AND ({_NEW_CLAIM_MISMATCH})
    BEGIN
      SELECT RAISE(ABORT, 'draft_claim evidence must belong to draft candidate');
    END
    """,
    f"""
    CREATE TRIGGER draft_claim_evidence_candidate_update
    BEFORE UPDATE OF draft_id, evidence_id ON draft_claim
    FOR EACH ROW
    WHEN NEW.evidence_id IS NOT NULL
     AND ({_NEW_CLAIM_MISMATCH})
    BEGIN
      SELECT RAISE(ABORT, 'draft_claim evidence must belong to draft candidate');
    END
    """,
    f"""
    CREATE TRIGGER send_confirmation_claim_candidate_insert
    BEFORE INSERT ON send_confirmation
    FOR EACH ROW
    WHEN {_CONFIRMATION_CLAIM_MISMATCH}
    BEGIN
      SELECT RAISE(ABORT, 'approved draft claims must belong to recipient candidate');
    END
    """,
    f"""
    CREATE TRIGGER approved_score_signal_insert_collision
    BEFORE INSERT ON score_signal
    FOR EACH ROW
    WHEN {_NEW_SIGNAL_APPROVED}
    BEGIN
      SELECT RAISE(ABORT, 'approved score_signal identity and root are immutable');
    END
    """,
    f"""
    CREATE TRIGGER approved_score_signal_update
    BEFORE UPDATE OF id, score_id ON score_signal
    FOR EACH ROW
    WHEN (
         (
           (NEW.id IS NOT OLD.id OR NEW.score_id IS NOT OLD.score_id)
           AND ({_OLD_SIGNAL_APPROVED})
         )
      OR (
           NEW.id IS NOT OLD.id
           AND ({_NEW_SIGNAL_APPROVED})
         )
    )
    BEGIN
      SELECT RAISE(ABORT, 'approved score_signal identity and root are immutable');
    END
    """,
    f"""
    CREATE TRIGGER approved_score_signal_delete
    BEFORE DELETE ON score_signal
    FOR EACH ROW
    WHEN ({_OLD_SIGNAL_APPROVED})
     AND EXISTS (
       SELECT 1
         FROM score
         JOIN candidate ON candidate.id = score.candidate_id
         JOIN session ON session.id = candidate.session_id
        WHERE score.id = OLD.score_id
     )
    BEGIN
      SELECT RAISE(ABORT, 'approved score_signal may be deleted only by session purge');
    END
    """,
)


def apply(connection: Connection) -> None:
    if connection.exec_driver_sql(_PREFLIGHT).scalar_one():
        raise RuntimeError(
            f"cannot apply {VERSION}: cross-candidate draft_claim evidence"
        )
    for statement in STATEMENTS:
        connection.exec_driver_sql(statement)
