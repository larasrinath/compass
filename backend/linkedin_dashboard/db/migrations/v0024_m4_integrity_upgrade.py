"""Upgrade M4 invariants without rewriting the applied v0023 migration."""

# ruff: noqa: E501 -- keeping each SQLite trigger clause visible aids review.

from __future__ import annotations

from sqlalchemy import Connection

VERSION = "0024_m4_integrity_upgrade"

TRIGGER_NAMES = (
    "section_error_is_immutable",
    "section_error_insert_collision_all",
    "section_error_no_delete_all",
    "score_signal_before_finalize",
    "score_signal_m4_insert_collision",
    "score_signal_m4_no_delete",
    "phase_gate_manifest_insert",
)

_LATEST_ROOTED_ERROR_FROM = """
FROM section_error canonical_error
JOIN profile_fetch canonical_fetch
  ON canonical_fetch.id=canonical_error.fetch_id
 AND canonical_fetch.candidate_id=s.candidate_id
JOIN json_each(json_extract(
  canonical_fetch.projection_payload,'$.section_errors')) canonical_item
WHERE canonical_error.candidate_id=s.candidate_id
  AND canonical_error.search_run_id IS NULL
  AND canonical_error.section_name=json_extract(snap.value,'$.name')
  AND canonical_fetch.contract_error IS NULL
  AND canonical_fetch.raw_response IS NOT NULL
  AND canonical_fetch.raw_response<>'null'
  AND canonical_item.key=canonical_error.section_name
  AND json(canonical_item.value)=json(canonical_error.source_item)
  AND json_extract(canonical_item.value,'$.error_type')=canonical_error.error_type
  AND json_extract(canonical_item.value,'$.error_message')=canonical_error.error_message
ORDER BY canonical_fetch.finished_at DESC NULLS LAST,
  canonical_fetch.started_at DESC,canonical_fetch.id DESC,canonical_error.id DESC
LIMIT 1
"""

_LATEST_ROOTED_ERROR_ID = f"(SELECT canonical_error.id {_LATEST_ROOTED_ERROR_FROM})"
_LATEST_ROOTED_ERROR_REASON = (
    "(SELECT CASE WHEN lower(canonical_error.error_type)='rate_limit' "
    f"THEN 'rate_limit' ELSE 'fetch_error' END {_LATEST_ROOTED_ERROR_FROM})"
)

STATEMENTS = (
    *(f'DROP TRIGGER IF EXISTS "{name}"' for name in TRIGGER_NAMES),
    """CREATE TRIGGER section_error_is_immutable BEFORE UPDATE ON section_error
       FOR EACH ROW
       BEGIN SELECT RAISE(ABORT, 'section error history is immutable'); END""",
    """CREATE TRIGGER section_error_insert_collision_all
       BEFORE INSERT ON section_error FOR EACH ROW WHEN EXISTS (
         SELECT 1 FROM section_error old WHERE old.id=NEW.id)
       BEGIN SELECT RAISE(ABORT, 'section error already exists'); END""",
    """CREATE TRIGGER section_error_no_delete_all BEFORE DELETE ON section_error
       FOR EACH ROW WHEN
         EXISTS (SELECT 1 FROM candidate c JOIN session root ON root.id=c.session_id
                 WHERE c.id=OLD.candidate_id)
         OR EXISTS (SELECT 1 FROM search_run sr JOIN session root
                    ON root.id=sr.session_id WHERE sr.id=OLD.search_run_id)
       BEGIN SELECT RAISE(ABORT, 'section error history is append-only'); END""",
    """CREATE TRIGGER score_signal_before_finalize BEFORE INSERT ON score_signal
       FOR EACH ROW WHEN NOT EXISTS (
         SELECT 1 FROM score s WHERE s.id=NEW.score_id
           AND s.scoring_config_id IS NOT NULL
           AND s.is_current=0 AND s.superseded_at IS NULL)
       BEGIN SELECT RAISE(ABORT, 'only staged M4 scores accept new signals'); END""",
    """CREATE TRIGGER score_signal_m4_insert_collision
       BEFORE INSERT ON score_signal FOR EACH ROW WHEN EXISTS (
         SELECT 1 FROM score_signal old JOIN score s ON s.id=old.score_id
         JOIN candidate c ON c.id=s.candidate_id
         JOIN session root ON root.id=c.session_id WHERE old.id=NEW.id)
       BEGIN SELECT RAISE(ABORT, 'M4 score signal already exists'); END""",
    """CREATE TRIGGER score_signal_m4_no_delete BEFORE DELETE ON score_signal
       FOR EACH ROW WHEN EXISTS (
         SELECT 1 FROM score s JOIN candidate c ON c.id=s.candidate_id
         JOIN session root ON root.id=c.session_id
         WHERE s.id=OLD.score_id)
       BEGIN SELECT RAISE(ABORT, 'M4 score signal is append-only'); END""",
    f"""CREATE TRIGGER phase_gate_manifest_insert BEFORE INSERT ON phase_gate
       FOR EACH ROW WHEN (NEW.gate IN ('A','C') AND json_array_length(NEW.evidence_manifest)<>0)
         OR (NEW.gate='B' AND (
           json_type(NEW.evidence_manifest)<>'array'
           OR (SELECT count(DISTINCT json_extract(item.value,'$.evidence_id'))
               FROM json_each(NEW.evidence_manifest) item)<10
           OR EXISTS (SELECT 1 FROM json_each(NEW.evidence_manifest) item
             WHERE NOT EXISTS (
               SELECT 1 FROM evidence e JOIN evidence_set es
                 ON es.id=e.evidence_set_id
               JOIN score_signal ss ON ss.id=e.score_signal_id
               JOIN score_claim claim ON claim.evidence_set_id=es.id
                 AND claim.score_signal_id=ss.id
                 AND claim.verdict IN ('matched','contradicted')
               JOIN score s ON s.id=ss.score_id AND s.candidate_id=es.candidate_id
               JOIN role_brief rb ON rb.id=s.brief_id AND rb.superseded_at IS NULL
               JOIN scoring_config cfg ON cfg.id=s.scoring_config_id
                 AND cfg.superseded_at IS NULL
               JOIN candidate c ON c.id=s.candidate_id
               JOIN profile_section ps ON ps.id=e.profile_section_id
               JOIN score_input_section source ON source.score_id=s.id
                 AND source.profile_section_id=ps.id
                 AND source.content_sha256=ps.content_sha256
               WHERE e.id=json_extract(item.value,'$.evidence_id')
                 AND s.id=json_extract(item.value,'$.score_id') AND s.is_current=1
                 AND s.input_fingerprint=json_extract(item.value,'$.input_fingerprint')
                 AND c.session_id=NEW.session_id
                 AND ps.content_sha256=e.content_sha256
                 AND substr(ps.raw_text,e.span_start+1,e.span_end-e.span_start)=e.snippet
                 AND NOT EXISTS (
                   SELECT 1 FROM json_each(s.source_snapshot,'$.profile_snapshot.sections') snap
                   WHERE (json_extract(snap.value,'$.state')='complete' AND NOT EXISTS (
                     SELECT 1 FROM score_input_section expected
                     JOIN profile_section expected_ps
                       ON expected_ps.id=expected.profile_section_id
                     WHERE expected.score_id=s.id
                       AND expected.profile_section_id=json_extract(snap.value,'$.id')
                       AND expected.content_sha256=json_extract(snap.value,'$.content_sha256')
                       AND NOT EXISTS (
                         SELECT 1 FROM profile_section newer
                         WHERE newer.candidate_id=s.candidate_id
                           AND newer.section_name=json_extract(snap.value,'$.name')
                           AND (newer.retrieved_at>expected_ps.retrieved_at
                             OR (newer.retrieved_at=expected_ps.retrieved_at
                               AND newer.id>expected_ps.id)))))
                     OR (json_extract(snap.value,'$.state')='missing' AND EXISTS (
                       SELECT 1 FROM profile_section now_present
                       WHERE now_present.candidate_id=s.candidate_id
                         AND now_present.section_name=json_extract(snap.value,'$.name'))))))))
         OR (NEW.gate='B' AND EXISTS (
           SELECT 1 FROM json_each(NEW.evidence_manifest) item
           JOIN score s ON s.id=json_extract(item.value,'$.score_id')
             AND s.input_fingerprint=json_extract(item.value,'$.input_fingerprint')
             AND s.is_current=1
           JOIN candidate c ON c.id=s.candidate_id AND c.session_id=NEW.session_id
           JOIN json_each(
             s.source_snapshot,'$.profile_snapshot.sections') snap
           WHERE json_extract(snap.value,'$.state')='missing' AND (
             EXISTS (SELECT 1 FROM profile_section now_present
               WHERE now_present.candidate_id=s.candidate_id
                 AND now_present.section_name=json_extract(snap.value,'$.name'))
             OR NOT (
               json_extract(snap.value,'$.section_error_id') IS
                 {_LATEST_ROOTED_ERROR_ID}
               AND json_extract(snap.value,'$.missing_reason') IS
                 coalesce({_LATEST_ROOTED_ERROR_REASON},'not_requested')))))
       BEGIN SELECT RAISE(ABORT, 'Gate B requires ten current exact evidence spans'); END""",
)


def apply(connection: Connection) -> None:
    for statement in STATEMENTS:
        connection.exec_driver_sql(statement)
