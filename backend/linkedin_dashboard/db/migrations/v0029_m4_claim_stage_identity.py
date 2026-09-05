"""Bind every claim identity and finalized claim set to the immutable brief."""

# ruff: noqa: E501 -- SQLite invariant clauses are kept together for review.

from __future__ import annotations

from sqlalchemy import Connection

from linkedin_dashboard.db.migrations import v0025_m4_semantic_integrity as v25

VERSION = "0029_m4_claim_stage_identity"

_DISPLAYS = """(SELECT group_concat(display, ', ') FROM (
  SELECT json_extract(input.value,'$.display') AS display
  FROM json_each(rb.scoring_inputs,'$."' || ss.signal_id || '"') input
  ORDER BY cast(input.key AS INTEGER)))"""

_CLAIM_BOUND = f"""
typeof(claim.claim_key)='text' AND typeof(claim.display_term)='text'
AND {v25._CLAIM_IS_BRIEF_BOUND}
AND (ss.signal_id IN ('S-1','S-2','S-8')
  OR (ss.signal_id='S-3' AND claim.display_term=
      cast(rb.required_experience_months AS TEXT) || ' months relevant experience')
  OR (ss.signal_id='S-4' AND (
    (claim.verdict='matched' AND EXISTS (
      SELECT 1 FROM json_each(rb.scoring_inputs,'$."S-4"') input
      WHERE claim.display_term=json_extract(input.value,'$.display')))
    OR (claim.verdict<>'matched' AND claim.display_term={_DISPLAYS})))
  OR (ss.signal_id='S-5' AND claim.display_term={_DISPLAYS})
  OR (ss.signal_id='S-6' AND claim.display_term=
      json_extract(rb.scoring_inputs,'$."S-6"[0].display')))
"""

# Unique(score_signal_id, claim_key), exact identity validation and this exact
# count together prove there are no missing, extra or duplicated brief claims.
_CLAIM_SET_INVALID = f"""
EXISTS (SELECT 1 FROM score_signal ss WHERE ss.score_id=s.id AND (
  (SELECT count(*) FROM score_claim claim WHERE claim.score_signal_id=ss.id)<>
    CASE WHEN ss.signal_id IN ('S-1','S-2','S-8')
      THEN json_array_length(rb.scoring_inputs,'$."' || ss.signal_id || '"')
      ELSE 1 END
  OR EXISTS (SELECT 1 FROM score_claim claim WHERE claim.score_signal_id=ss.id
    AND NOT ({_CLAIM_BOUND}))))
"""

TRIGGER_NAMES = (
    "score_claim_brief_identity_v29",
    "score_finalize_claim_set_v29",
    "score_finalize_stage_v29",
    "phase_gate_claims_stage_v29",
)

STATEMENTS = (
    f"""CREATE TRIGGER score_claim_brief_identity_v29
       BEFORE INSERT ON score_claim FOR EACH ROW WHEN NOT EXISTS (
         SELECT 1 FROM score_signal ss JOIN score s ON s.id=ss.score_id
         JOIN role_brief rb ON rb.id=s.brief_id
         WHERE ss.id=NEW.score_signal_id AND ({_CLAIM_BOUND.replace("claim.", "NEW.")}))
       BEGIN SELECT RAISE(ABORT, 'score claim semantics do not match brief: invalid identity'); END""",
    f"""CREATE TRIGGER score_finalize_claim_set_v29
       BEFORE UPDATE OF is_current ON score FOR EACH ROW
       WHEN OLD.is_current=0 AND NEW.is_current=1 AND EXISTS (
         SELECT 1 FROM score s JOIN role_brief rb ON rb.id=s.brief_id
         WHERE s.id=NEW.id AND ({_CLAIM_SET_INVALID}))
       BEGIN SELECT RAISE(ABORT, 'score claim set does not match brief'); END""",
    """CREATE TRIGGER score_finalize_stage_v29
       BEFORE UPDATE OF is_current ON score FOR EACH ROW
       WHEN OLD.is_current=0 AND NEW.is_current=1 AND (
         json_extract(NEW.source_snapshot,'$.scoring_stage') IS NOT NEW.stage
         OR NOT EXISTS (SELECT 1 FROM candidate c WHERE c.id=NEW.candidate_id
           AND NEW.stage=CASE WHEN c.stage='stage2' THEN 'enriched' ELSE 'provisional' END))
       BEGIN SELECT RAISE(ABORT, 'current score is incomplete or inconsistent: score stage does not match immutable inputs'); END""",
    f"""CREATE TRIGGER phase_gate_claims_stage_v29
       BEFORE INSERT ON phase_gate FOR EACH ROW WHEN NEW.gate='B' AND EXISTS (
         SELECT 1 FROM json_each(NEW.evidence_manifest) item
         JOIN score s ON s.id=json_extract(item.value,'$.score_id')
         JOIN role_brief rb ON rb.id=s.brief_id
         JOIN candidate c ON c.id=s.candidate_id
         WHERE s.stage<>CASE WHEN c.stage='stage2' THEN 'enriched' ELSE 'provisional' END
           OR ({_CLAIM_SET_INVALID}))
       BEGIN SELECT RAISE(ABORT, 'Gate B claims or stage do not match current brief inputs'); END""",
)


def apply(connection: Connection) -> None:
    # Existing immutable claims must pass, including superseded history. Never
    # silently relabel or discard prior evidence to make an upgrade succeed.
    invalid = connection.exec_driver_sql(
        f"""SELECT claim.id,c.session_id FROM score_claim claim
        JOIN score_signal ss ON ss.id=claim.score_signal_id
        JOIN score s ON s.id=ss.score_id JOIN role_brief rb ON rb.id=s.brief_id
        JOIN candidate c ON c.id=s.candidate_id
        WHERE NOT ({_CLAIM_BOUND}) LIMIT 1"""
    ).first()
    if invalid is None:
        invalid = connection.exec_driver_sql(
            f"""SELECT s.id,c.session_id FROM score s
            JOIN role_brief rb ON rb.id=s.brief_id
            JOIN candidate c ON c.id=s.candidate_id
            WHERE (s.is_current=1 OR s.superseded_at IS NOT NULL)
              AND ({_CLAIM_SET_INVALID}) LIMIT 1"""
        ).first()
    if invalid is not None:
        raise RuntimeError(
            f"cannot apply {VERSION}: immutable claim identity/set for row "
            f"{invalid[0]} does not match brief; purge owning session "
            f"{invalid[1]!r} through the supported session-purge workflow "
            "or restore a known-good backup before retrying"
        )
    # Legacy score fingerprints remain immutable. A local rescore appends the
    # new stage-bound identity; the Gate-B guard rejects a legacy stale stage
    # even before that rescore takes place.
    for statement in STATEMENTS:
        connection.exec_driver_sql(statement)
