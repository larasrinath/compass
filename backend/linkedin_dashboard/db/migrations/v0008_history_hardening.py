from __future__ import annotations

from sqlalchemy import Connection

VERSION = "0008_history_hardening"

_INVALID_CONFIRM_STATE = """
       (confirm_send = 1
        AND state NOT IN ('SENDING','SENT','FAILED_CONCLUSIVE','AMBIGUOUS'))
    OR (confirm_send = 0 AND state NOT IN ('DRY_RUN_OK','DRY_RUN_FAILED'))
"""

_PREFLIGHTS = (
    (
        "confirm_send state family",
        f"SELECT EXISTS (SELECT 1 FROM send_attempt WHERE {_INVALID_CONFIRM_STATE})",
    ),
    (
        "send confirmation provenance",
        """
        SELECT EXISTS (
            SELECT 1
              FROM send_confirmation AS confirmation
              LEFT JOIN message_draft AS draft ON draft.id = confirmation.draft_id
             WHERE draft.id IS NULL
                OR confirmation.candidate_id IS NOT draft.candidate_id
                OR confirmation.body_sha256 IS NOT draft.body_sha256
                OR confirmation.token IS NULL
                OR confirmation.created_at IS NULL
                OR confirmation.expires_at IS NULL
        )
        """,
    ),
)

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

STATEMENTS = (
    "DROP TRIGGER IF EXISTS audit_log_insert_collision",
    "DROP TRIGGER IF EXISTS send_attempt_insert_id_collision",
    "DROP TRIGGER IF EXISTS send_attempt_insert_idempotency_collision",
    "DROP TRIGGER IF EXISTS send_attempt_insert_live_collision",
    "DROP TRIGGER IF EXISTS send_confirmation_insert_collision",
    "DROP TRIGGER IF EXISTS send_confirmation_is_immutable",
    "DROP TRIGGER IF EXISTS send_attempt_confirm_state_insert",
    "DROP TRIGGER IF EXISTS send_attempt_confirm_state_update",
    "DROP TRIGGER IF EXISTS candidate_recipient_identity_is_immutable",
    "DROP TRIGGER IF EXISTS candidate_send_reference_insert_collision",
    "DROP TRIGGER IF EXISTS candidate_with_attempt_no_direct_delete",
    "DROP TRIGGER IF EXISTS candidate_with_send_reference_no_direct_delete",
    "DROP TRIGGER IF EXISTS referenced_message_draft_insert_collision",
    "DROP TRIGGER IF EXISTS approved_draft_claim_insert",
    "DROP TRIGGER IF EXISTS approved_draft_claim_update",
    "DROP TRIGGER IF EXISTS approved_draft_claim_delete",
    "DROP TRIGGER IF EXISTS approved_evidence_insert_collision",
    "DROP TRIGGER IF EXISTS approved_evidence_update",
    "DROP TRIGGER IF EXISTS approved_evidence_delete",
    """
    CREATE TRIGGER audit_log_insert_collision
    BEFORE INSERT ON audit_log
    FOR EACH ROW
    WHEN EXISTS (SELECT 1 FROM audit_log WHERE id = NEW.id)
    BEGIN
      SELECT RAISE(ABORT, 'audit_log is append-only; id already exists');
    END
    """,
    """
    CREATE TRIGGER send_attempt_insert_id_collision
    BEFORE INSERT ON send_attempt
    FOR EACH ROW
    WHEN EXISTS (SELECT 1 FROM send_attempt WHERE id = NEW.id)
    BEGIN
      SELECT RAISE(ABORT, 'send_attempt id already exists');
    END
    """,
    """
    CREATE TRIGGER send_attempt_insert_idempotency_collision
    BEFORE INSERT ON send_attempt
    FOR EACH ROW
    WHEN EXISTS (
      SELECT 1 FROM send_attempt WHERE idempotency_key = NEW.idempotency_key
    )
    BEGIN
      SELECT RAISE(ABORT, 'send_attempt idempotency key already exists');
    END
    """,
    """
    CREATE TRIGGER send_attempt_insert_live_collision
    BEFORE INSERT ON send_attempt
    FOR EACH ROW
    WHEN NEW.confirm_send = 1
     AND (
          NEW.state IN ('SENDING', 'SENT')
       OR (NEW.state = 'AMBIGUOUS'
           AND NEW.resolution IN ('unresolved', 'confirmed_sent'))
     )
     AND EXISTS (
       SELECT 1 FROM send_attempt
        WHERE candidate_id = NEW.candidate_id
          AND confirm_send = 1
          AND (
               state IN ('SENDING', 'SENT')
            OR (state = 'AMBIGUOUS'
                AND resolution IN ('unresolved', 'confirmed_sent'))
          )
     )
    BEGIN
      SELECT RAISE(ABORT, 'a live send_attempt already exists for candidate');
    END
    """,
    """
    CREATE TRIGGER send_confirmation_insert_collision
    BEFORE INSERT ON send_confirmation
    FOR EACH ROW
    WHEN EXISTS (SELECT 1 FROM send_confirmation WHERE token = NEW.token)
    BEGIN
      SELECT RAISE(ABORT, 'send_confirmation token already exists');
    END
    """,
    """
    CREATE TRIGGER send_confirmation_is_immutable
    BEFORE UPDATE ON send_confirmation
    FOR EACH ROW
    WHEN NEW.token IS NOT OLD.token
      OR NEW.candidate_id IS NOT OLD.candidate_id
      OR NEW.draft_id IS NOT OLD.draft_id
      OR NEW.body_sha256 IS NOT OLD.body_sha256
      OR NEW.created_at IS NOT OLD.created_at
      OR NEW.expires_at IS NOT OLD.expires_at
      OR (OLD.consumed_at IS NOT NULL AND NEW.consumed_at IS NOT OLD.consumed_at)
    BEGIN
      SELECT CASE
        WHEN OLD.consumed_at IS NOT NULL
         AND NEW.consumed_at IS NOT OLD.consumed_at
        THEN RAISE(ABORT, 'send_confirmation consumed_at may be set exactly once')
        ELSE RAISE(ABORT, 'send_confirmation is immutable except first consumption')
      END;
    END
    """,
    """
    CREATE TRIGGER send_attempt_confirm_state_insert
    BEFORE INSERT ON send_attempt
    FOR EACH ROW
    WHEN (NEW.confirm_send = 1
          AND NEW.state NOT IN ('SENDING','SENT','FAILED_CONCLUSIVE','AMBIGUOUS'))
      OR (NEW.confirm_send = 0
          AND NEW.state NOT IN ('DRY_RUN_OK','DRY_RUN_FAILED'))
    BEGIN
      SELECT RAISE(ABORT, 'send_attempt violates confirm_send state family');
    END
    """,
    """
    CREATE TRIGGER send_attempt_confirm_state_update
    BEFORE UPDATE ON send_attempt
    FOR EACH ROW
    WHEN (NEW.confirm_send = 1
          AND NEW.state NOT IN ('SENDING','SENT','FAILED_CONCLUSIVE','AMBIGUOUS'))
      OR (NEW.confirm_send = 0
          AND NEW.state NOT IN ('DRY_RUN_OK','DRY_RUN_FAILED'))
    BEGIN
      SELECT RAISE(ABORT, 'send_attempt violates confirm_send state family');
    END
    """,
    """
    CREATE TRIGGER candidate_recipient_identity_is_immutable
    BEFORE UPDATE ON candidate
    FOR EACH ROW
    WHEN (
         EXISTS (SELECT 1 FROM send_confirmation WHERE candidate_id = OLD.id)
      OR EXISTS (SELECT 1 FROM send_attempt WHERE candidate_id = OLD.id)
    )
    AND (
         NEW.username IS NOT OLD.username
      OR NEW.profile_url IS NOT OLD.profile_url
      OR NEW.profile_urn IS NOT OLD.profile_urn
    )
    BEGIN
      SELECT RAISE(ABORT, 'approved candidate recipient identity is immutable');
    END
    """,
    """
    CREATE TRIGGER candidate_send_reference_insert_collision
    BEFORE INSERT ON candidate
    FOR EACH ROW
    WHEN EXISTS (
      SELECT 1
        FROM candidate AS existing
       WHERE (
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
    """
    CREATE TRIGGER candidate_with_send_reference_no_direct_delete
    BEFORE DELETE ON candidate
    FOR EACH ROW
    WHEN (
         EXISTS (SELECT 1 FROM send_confirmation WHERE candidate_id = OLD.id)
      OR EXISTS (SELECT 1 FROM send_attempt WHERE candidate_id = OLD.id)
    )
    AND EXISTS (SELECT 1 FROM session WHERE id = OLD.session_id)
    BEGIN
      SELECT RAISE(
        ABORT,
        'candidate with send history may be deleted only by full-session purge'
      );
    END
    """,
    """
    CREATE TRIGGER referenced_message_draft_insert_collision
    BEFORE INSERT ON message_draft
    FOR EACH ROW
    WHEN EXISTS (
      SELECT 1
        FROM message_draft AS existing
       WHERE (
              existing.id = NEW.id
           OR (existing.candidate_id = NEW.candidate_id
               AND existing.version = NEW.version)
       )
         AND (
              EXISTS (
                SELECT 1 FROM send_confirmation WHERE draft_id = existing.id
              )
           OR EXISTS (
                SELECT 1 FROM send_attempt WHERE draft_id = existing.id
              )
         )
    )
    BEGIN
      SELECT RAISE(ABORT, 'referenced message_draft already exists');
    END
    """,
    f"""
    CREATE TRIGGER approved_draft_claim_insert
    BEFORE INSERT ON draft_claim
    FOR EACH ROW
    WHEN ({_DRAFT_REFERENCED.format(draft_id="NEW.draft_id")})
      OR EXISTS (
        SELECT 1 FROM draft_claim AS existing
         WHERE existing.id = NEW.id
           AND ({_DRAFT_REFERENCED.format(draft_id="existing.draft_id")})
      )
    BEGIN
      SELECT RAISE(ABORT, 'approved draft_claim is immutable');
    END
    """,
    f"""
    CREATE TRIGGER approved_draft_claim_update
    BEFORE UPDATE ON draft_claim
    FOR EACH ROW
    WHEN ({_DRAFT_REFERENCED.format(draft_id="OLD.draft_id")})
      OR ({_DRAFT_REFERENCED.format(draft_id="NEW.draft_id")})
    BEGIN
      SELECT RAISE(ABORT, 'approved draft_claim is immutable');
    END
    """,
    f"""
    CREATE TRIGGER approved_draft_claim_delete
    BEFORE DELETE ON draft_claim
    FOR EACH ROW
    WHEN ({_DRAFT_REFERENCED.format(draft_id="OLD.draft_id")})
     AND EXISTS (
       SELECT 1
         FROM message_draft AS draft
         JOIN candidate ON candidate.id = draft.candidate_id
         JOIN session ON session.id = candidate.session_id
        WHERE draft.id = OLD.draft_id
     )
    BEGIN
      SELECT RAISE(ABORT, 'approved draft_claim is immutable');
    END
    """,
    f"""
    CREATE TRIGGER approved_evidence_insert_collision
    BEFORE INSERT ON evidence
    FOR EACH ROW
    WHEN {_EVIDENCE_APPROVED.format(evidence_id="NEW.id")}
    BEGIN
      SELECT RAISE(ABORT, 'approved evidence is immutable');
    END
    """,
    f"""
    CREATE TRIGGER approved_evidence_update
    BEFORE UPDATE ON evidence
    FOR EACH ROW
    WHEN {_EVIDENCE_APPROVED.format(evidence_id="OLD.id")}
    BEGIN
      SELECT RAISE(ABORT, 'approved evidence is immutable');
    END
    """,
    f"""
    CREATE TRIGGER approved_evidence_delete
    BEFORE DELETE ON evidence
    FOR EACH ROW
    WHEN {_EVIDENCE_APPROVED.format(evidence_id="OLD.id")}
     AND EXISTS (
       SELECT 1
         FROM draft_claim AS claim
         JOIN message_draft AS draft ON draft.id = claim.draft_id
         JOIN candidate ON candidate.id = draft.candidate_id
         JOIN session ON session.id = candidate.session_id
        WHERE claim.evidence_id = OLD.id
     )
    BEGIN
      SELECT RAISE(ABORT, 'approved evidence is immutable');
    END
    """,
)


def apply(connection: Connection) -> None:
    for label, statement in _PREFLIGHTS:
        if connection.exec_driver_sql(statement).scalar_one():
            raise RuntimeError(f"cannot apply {VERSION}: incompatible {label}")
    for statement in STATEMENTS:
        connection.exec_driver_sql(statement)
