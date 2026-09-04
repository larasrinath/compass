"""Bind finalized M4 scores and claims to their immutable inputs."""

# ruff: noqa: E501 -- keeping each SQLite invariant clause visible aids review.

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Connection

from linkedin_dashboard.db.scoring_manifest import SIGNAL_IDS, build_manifest
from linkedin_dashboard.db.unicode_identity import (
    SCORING_CANONICAL_SENTINEL,
    SCORING_DISPLAY_CANONICAL_SENTINEL,
)

VERSION = "0025_m4_semantic_integrity"


def _signal_source(signal_id: str, brief: str) -> str:
    sources = {
        "S-1": f"SELECT term,aliases FROM brief_skill WHERE brief_id={brief}.id AND kind='required'",
        "S-2": f"SELECT term,aliases FROM brief_skill WHERE brief_id={brief}.id AND kind='optional'",
        "S-3": (
            f"SELECT term,aliases FROM brief_term WHERE brief_id={brief}.id AND kind='target_title' "
            "UNION ALL "
            f"SELECT term,aliases FROM brief_skill WHERE brief_id={brief}.id AND kind='required'"
        ),
        "S-4": f"SELECT term,aliases FROM brief_term WHERE brief_id={brief}.id AND kind='target_title'",
        "S-5": f"SELECT term,aliases FROM brief_term WHERE brief_id={brief}.id AND kind='industry'",
        "S-6": f"SELECT {brief}.location AS term,json_array() AS aliases WHERE length(trim({brief}.location))>0",
        "S-8": f"SELECT term,aliases FROM brief_credential WHERE brief_id={brief}.id",
    }
    return sources[signal_id]


def _signal_manifest_invalid(signal_id: str, brief: str) -> str:
    path = f"'$.\"{signal_id}\"'"
    source = _signal_source(signal_id, brief)
    return f"""
json_type({brief}.scoring_inputs,{path})<>'array'
OR EXISTS (
  SELECT 1 FROM json_each({brief}.scoring_inputs,{path}) item
  WHERE json_type(item.value)<>'object'
    OR (SELECT count(*) FROM json_each(item.value))<>3
    OR json_type(item.value,'$.display')<>'text'
    OR length(json_extract(item.value,'$.display'))=0
    OR NOT (json_extract(item.value,'$.display')=
            '{SCORING_DISPLAY_CANONICAL_SENTINEL}'
              COLLATE scoring_display_canonical_v1)
    OR json_type(item.value,'$.term')<>'text'
    OR NOT (json_extract(item.value,'$.term')=
            '{SCORING_CANONICAL_SENTINEL}' COLLATE scoring_canonical_v1)
    OR json_type(item.value,'$.aliases')<>'array'
    OR EXISTS (SELECT 1 FROM json_each(json_extract(item.value,'$.aliases')) alias
      WHERE alias.type<>'text' OR NOT (
        alias.value='{SCORING_CANONICAL_SENTINEL}' COLLATE scoring_canonical_v1))
    OR EXISTS (SELECT 1 FROM json_each(json_extract(item.value,'$.aliases')) current
      JOIN json_each(json_extract(item.value,'$.aliases')) previous
        ON cast(previous.key AS INTEGER)=cast(current.key AS INTEGER)-1
      WHERE previous.value>=current.value)
    OR EXISTS (SELECT 1 FROM json_each({brief}.scoring_inputs,{path}) primary_item,
                            json_each(json_extract(item.value,'$.aliases')) alias
      WHERE json_extract(primary_item.value,'$.term')=alias.value)
    OR NOT (json_extract(item.value,'$.display')=(
      SELECT min(display_source.term COLLATE scoring_display_v1)
      FROM ({source}) display_source
      WHERE display_source.term=json_extract(item.value,'$.term')
        COLLATE scoring_normalized_v1
    ) COLLATE scoring_display_v1))
OR EXISTS (
  SELECT 1 FROM json_each({brief}.scoring_inputs,{path}) current
  JOIN json_each({brief}.scoring_inputs,{path}) previous
    ON cast(previous.key AS INTEGER)=cast(current.key AS INTEGER)-1
  WHERE json_extract(previous.value,'$.term')>=json_extract(current.value,'$.term'))
OR json_array_length({brief}.scoring_inputs,{path})<>(
  SELECT count(DISTINCT source.term COLLATE scoring_normalized_v1)
  FROM ({source}) source)
OR EXISTS (SELECT 1 FROM ({source}) source WHERE NOT EXISTS (
  SELECT 1 FROM json_each({brief}.scoring_inputs,{path}) item
  WHERE json_extract(item.value,'$.term')=source.term COLLATE scoring_normalized_v1))
OR EXISTS (SELECT 1 FROM json_each({brief}.scoring_inputs,{path}) item WHERE NOT EXISTS (
  SELECT 1 FROM ({source}) source
  WHERE source.term=json_extract(item.value,'$.term') COLLATE scoring_normalized_v1))
OR EXISTS (
  SELECT 1 FROM json_each({brief}.scoring_inputs,{path}) item,
                json_each(json_extract(item.value,'$.aliases')) manifest_alias
  WHERE NOT EXISTS (
    SELECT 1 FROM ({source}) source,json_each(source.aliases) raw_alias
    WHERE source.term=json_extract(item.value,'$.term') COLLATE scoring_normalized_v1
      AND raw_alias.value=manifest_alias.value COLLATE scoring_normalized_v1)
  OR EXISTS (SELECT 1 FROM ({source}) primary_source
    WHERE primary_source.term=manifest_alias.value COLLATE scoring_normalized_v1)
  OR NOT (json_extract(item.value,'$.term')=(
    SELECT min(owner.term COLLATE scoring_normalized_v1)
    FROM ({source}) owner,json_each(owner.aliases) owner_alias
    WHERE owner_alias.value=manifest_alias.value COLLATE scoring_normalized_v1
  ) COLLATE scoring_normalized_v1))
OR EXISTS (
  SELECT 1 FROM ({source}) source,json_each(source.aliases) raw_alias
  WHERE NOT EXISTS (SELECT 1 FROM ({source}) primary_source
    WHERE primary_source.term=raw_alias.value COLLATE scoring_normalized_v1)
    AND source.term=(
      SELECT min(owner.term COLLATE scoring_normalized_v1)
      FROM ({source}) owner,json_each(owner.aliases) owner_alias
      WHERE owner_alias.value=raw_alias.value COLLATE scoring_normalized_v1
    ) COLLATE scoring_normalized_v1
    AND NOT EXISTS (
      SELECT 1 FROM json_each({brief}.scoring_inputs,{path}) item,
                    json_each(json_extract(item.value,'$.aliases')) manifest_alias
      WHERE json_extract(item.value,'$.term')=source.term COLLATE scoring_normalized_v1
        AND manifest_alias.value=raw_alias.value COLLATE scoring_normalized_v1))
"""


def _manifest_invalid(brief: str) -> str:
    signals = "\nOR ".join(
        f"({_signal_manifest_invalid(signal_id, brief)})" for signal_id in SIGNAL_IDS
    )
    allowed = ",".join(f"'{item}'" for item in (*SIGNAL_IDS, "matcher_version"))
    return f"""
json_valid({brief}.scoring_inputs)<>1
OR json_type({brief}.scoring_inputs)<>'object'
OR json_extract({brief}.scoring_inputs,'$.matcher_version')<>'scoring-v1'
OR (SELECT count(*) FROM json_each({brief}.scoring_inputs))<>8
OR EXISTS (SELECT 1 FROM json_each({brief}.scoring_inputs) item
           WHERE item.key NOT IN ({allowed}))
OR {signals}
"""


_EXPECTED_TERMS = """
(SELECT json_group_array(term_key) FROM (
  SELECT json_extract(input.value,'$.term') AS term_key
  FROM json_each(rb.scoring_inputs,'$."' || ss.signal_id || '"') input
  WHERE ss.signal_id NOT IN ('S-1','S-2','S-8')
    OR json_extract(input.value,'$.term')=substr(claim.claim_key,5)
  ORDER BY cast(input.key AS INTEGER)))
"""

_EXPECTED_ALIASES = """
(SELECT json_group_array(alias_key) FROM (
  SELECT DISTINCT alias.value AS alias_key
  FROM json_each(rb.scoring_inputs,'$."' || ss.signal_id || '"') input,
       json_each(json_extract(input.value,'$.aliases')) alias
  WHERE ss.signal_id NOT IN ('S-1','S-2','S-8')
    OR json_extract(input.value,'$.term')=substr(claim.claim_key,5)
  ORDER BY alias_key))
"""

_CLAIM_IS_BRIEF_BOUND = """
((ss.signal_id='S-1'
   AND EXISTS (SELECT 1 FROM json_each(rb.scoring_inputs,'$."S-1"') input
     WHERE claim.claim_key='S-1:' || json_extract(input.value,'$.term')
       AND claim.display_term=json_extract(input.value,'$.display')))
 OR (ss.signal_id='S-2'
   AND EXISTS (SELECT 1 FROM json_each(rb.scoring_inputs,'$."S-2"') input
     WHERE claim.claim_key='S-2:' || json_extract(input.value,'$.term')
       AND claim.display_term=json_extract(input.value,'$.display')))
 OR (ss.signal_id='S-8'
   AND EXISTS (SELECT 1 FROM json_each(rb.scoring_inputs,'$."S-8"') input
     WHERE claim.claim_key='S-8:' || json_extract(input.value,'$.term')
       AND claim.display_term=json_extract(input.value,'$.display')))
 OR (ss.signal_id='S-4' AND claim.claim_key='S-4:title-similarity'
   AND json_array_length(rb.scoring_inputs,'$."S-4"')>0)
 OR (ss.signal_id='S-5' AND claim.claim_key='S-5:industry-relevance'
   AND json_array_length(rb.scoring_inputs,'$."S-5"')>0)
 OR (ss.signal_id='S-6' AND claim.claim_key='S-6:location-fit'
   AND json_array_length(rb.scoring_inputs,'$."S-6"')=1)
 OR (ss.signal_id='S-3' AND claim.claim_key='S-3:experience-depth'
   AND coalesce(rb.required_experience_months,0)>0))
"""

_COVERAGE_SHAPE_INVALID = """
json_type(coverage.normalized_terms)<>'array'
OR json_array_length(coverage.normalized_terms)=0
OR EXISTS (SELECT 1 FROM json_each(coverage.normalized_terms) term
  WHERE term.type<>'text' OR NOT (
    term.value='__linkedin_dashboard_scoring_v1_canonical__'
      COLLATE scoring_canonical_v1))
OR EXISTS (SELECT 1 FROM json_each(coverage.normalized_terms) current
  JOIN json_each(coverage.normalized_terms) previous
    ON cast(previous.key AS INTEGER)=cast(current.key AS INTEGER)-1
  WHERE previous.value>=current.value)
OR json_type(coverage.aliases)<>'array'
OR EXISTS (SELECT 1 FROM json_each(coverage.aliases) alias
  WHERE alias.type<>'text' OR NOT (
    alias.value='__linkedin_dashboard_scoring_v1_canonical__'
      COLLATE scoring_canonical_v1))
OR EXISTS (SELECT 1 FROM json_each(coverage.aliases) current
  JOIN json_each(coverage.aliases) previous
    ON cast(previous.key AS INTEGER)=cast(current.key AS INTEGER)-1
  WHERE previous.value>=current.value)
OR EXISTS (SELECT 1 FROM json_each(coverage.aliases) alias,
                         json_each(coverage.normalized_terms) term
  WHERE alias.value=term.value)
OR coverage.matcher_version<>'scoring-v1'
"""

_BRIEF_MANIFEST_INVALID = _manifest_invalid("rb")

_FINALIZED_SIGNAL_INVALID = """
json_valid(s.source_snapshot)<>1
OR json_type(s.source_snapshot,'$.active_signal_ids')<>'array'
OR (SELECT count(*) FROM score_signal ss WHERE ss.score_id=s.id)<>
   json_array_length(s.source_snapshot,'$.active_signal_ids')
OR EXISTS (SELECT 1 FROM score_signal ss WHERE ss.score_id=s.id
  AND NOT EXISTS (SELECT 1 FROM json_each(
    s.source_snapshot,'$.active_signal_ids') active
    WHERE active.value=ss.signal_id))
OR EXISTS (SELECT 1 FROM json_each(
    s.source_snapshot,'$.active_signal_ids') active
  WHERE NOT EXISTS (SELECT 1 FROM score_signal ss
    WHERE ss.score_id=s.id AND ss.signal_id=active.value))
OR EXISTS (SELECT 1 FROM score_signal ss WHERE ss.score_id=s.id
  AND ss.weight IS NOT json_extract(
    cfg.weights,'$."' || ss.signal_id || '"'))
"""

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

TRIGGER_NAMES = (
    "role_brief_append_only",
    "role_brief_scoring_insert_v25",
    "role_brief_scoring_seal_v25",
    "score_claim_finalize_v25",
    "score_finalize_signal_set_v25",
    "signal_coverage_shape_v25",
    "phase_gate_manifest_insert",
)

STATEMENTS = (
    f"""CREATE TRIGGER role_brief_scoring_insert_v25
       BEFORE INSERT ON role_brief FOR EACH ROW
       WHEN NEW.sealed_at IS NOT NULL AND ({_manifest_invalid("NEW")})
       BEGIN SELECT RAISE(ABORT, 'role brief scoring inputs are not canonical'); END""",
    f"""CREATE TRIGGER role_brief_scoring_seal_v25
       BEFORE UPDATE OF sealed_at ON role_brief FOR EACH ROW
       WHEN OLD.sealed_at IS NULL AND NEW.sealed_at IS NOT NULL
         AND ({_manifest_invalid("NEW")})
       BEGIN SELECT RAISE(ABORT, 'role brief scoring inputs are not canonical'); END""",
    """CREATE TRIGGER role_brief_append_only BEFORE UPDATE ON role_brief
       FOR EACH ROW WHEN NEW.id IS NOT OLD.id OR NEW.session_id IS NOT OLD.session_id
         OR NEW.version IS NOT OLD.version OR NEW.created_at IS NOT OLD.created_at
         OR NEW.job_description IS NOT OLD.job_description
         OR NEW.target_titles IS NOT OLD.target_titles
         OR NEW.location IS NOT OLD.location OR NEW.industries IS NOT OLD.industries
         OR NEW.positive_keywords IS NOT OLD.positive_keywords
         OR NEW.negative_keywords IS NOT OLD.negative_keywords
         OR NEW.message_tone IS NOT OLD.message_tone
         OR NEW.required_experience_months IS NOT OLD.required_experience_months
         OR NEW.weights_version IS NOT OLD.weights_version
         OR NEW.scoring_inputs IS NOT OLD.scoring_inputs
         OR (NEW.sealed_at IS NOT OLD.sealed_at AND
             (OLD.sealed_at IS NOT NULL OR NEW.sealed_at IS NULL
              OR NEW.sealed_at IS NOT OLD.created_at))
         OR (NEW.superseded_at IS NOT OLD.superseded_at AND
             (OLD.superseded_at IS NOT NULL OR NEW.superseded_at IS NULL))
       BEGIN SELECT RAISE(ABORT, 'role brief versions are append-only'); END""",
    """CREATE UNIQUE INDEX IF NOT EXISTS score_signal_identity_v25
       ON score_signal(score_id,signal_id)""",
    f"""CREATE TRIGGER signal_coverage_shape_v25
       BEFORE INSERT ON signal_coverage FOR EACH ROW WHEN
         {_COVERAGE_SHAPE_INVALID.replace("coverage.", "NEW.")}
       BEGIN SELECT RAISE(ABORT, 'absence coverage is not canonical'); END""",
    f"""CREATE TRIGGER score_claim_finalize_v25
       BEFORE INSERT ON score_claim FOR EACH ROW WHEN
         (EXISTS (SELECT 1 FROM score_signal inactive
                  WHERE inactive.id=NEW.score_signal_id
                    AND inactive.signal_id='S-3')
          AND NOT EXISTS (
            SELECT 1 FROM score_signal active JOIN score s ON s.id=active.score_id
            JOIN role_brief rb ON rb.id=s.brief_id
            WHERE active.id=NEW.score_signal_id
              AND coalesce(rb.required_experience_months,0)>0))
         OR (NEW.verdict IN ('matched','contradicted') AND EXISTS (
           SELECT 1 FROM evidence e WHERE e.evidence_set_id=NEW.evidence_set_id
             AND ((NEW.verdict='matched' AND e.polarity<>'supporting')
               OR (NEW.verdict='contradicted' AND e.polarity<>'contradicting'))))
         OR (NEW.verdict='not_matched' AND NOT EXISTS (
           SELECT 1 FROM score_signal ss JOIN score s ON s.id=ss.score_id
           JOIN role_brief rb ON rb.id=s.brief_id
           JOIN coverage_set cs ON cs.id=NEW.coverage_set_id
             AND cs.score_signal_id=ss.id AND cs.candidate_id=s.candidate_id
           WHERE ss.id=NEW.score_signal_id
             AND NOT ({_BRIEF_MANIFEST_INVALID})
             AND {_CLAIM_IS_BRIEF_BOUND.replace("claim.", "NEW.")}
             AND EXISTS (SELECT 1 FROM signal_coverage coverage
                         WHERE coverage.coverage_set_id=cs.id)
             AND NOT EXISTS (
               SELECT 1 FROM signal_coverage coverage
               WHERE coverage.coverage_set_id=cs.id AND (
                 {_COVERAGE_SHAPE_INVALID}
                 OR json(coverage.normalized_terms)<>json({_EXPECTED_TERMS.replace("claim.", "NEW.")})
                 OR json(coverage.aliases)<>json({_EXPECTED_ALIASES.replace("claim.", "NEW.")})))))
       BEGIN SELECT RAISE(ABORT, 'score claim semantics do not match brief'); END""",
    f"""CREATE TRIGGER score_finalize_signal_set_v25
       BEFORE UPDATE OF is_current ON score FOR EACH ROW
       WHEN OLD.is_current=0 AND NEW.is_current=1 AND EXISTS (
         SELECT 1 FROM scoring_config cfg WHERE cfg.id=NEW.scoring_config_id
           AND ({_FINALIZED_SIGNAL_INVALID.replace("s.source_snapshot", "NEW.source_snapshot").replace("s.id", "NEW.id")})
       )
       BEGIN SELECT RAISE(ABORT, 'score signal set does not match snapshot'); END""",
    "DROP TRIGGER phase_gate_manifest_insert",
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
                 AND ((claim.verdict='matched' AND e.polarity='supporting')
                   OR (claim.verdict='contradicted' AND e.polarity='contradicting'))
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


def _array(value: Any) -> list[str]:
    decoded = json.loads(value) if isinstance(value, str) else value
    if not isinstance(decoded, list) or not all(
        isinstance(item, str) for item in decoded
    ):
        raise RuntimeError(f"cannot apply {VERSION}: brief aliases are invalid")
    return decoded


def _brief_manifest(
    connection: Connection, brief_id: str, location: str
) -> dict[str, object]:
    skills: dict[str, list[tuple[str, list[str]]]] = {
        "required": [],
        "optional": [],
    }
    for term, kind, aliases in connection.exec_driver_sql(
        "SELECT term,kind,aliases FROM brief_skill WHERE brief_id=? "
        "ORDER BY position,id",
        (brief_id,),
    ):
        skills[str(kind)].append((str(term), _array(aliases)))
    terms: dict[str, list[tuple[str, list[str]]]] = {
        "target_title": [],
        "industry": [],
    }
    for term, kind, aliases in connection.exec_driver_sql(
        "SELECT term,kind,aliases FROM brief_term WHERE brief_id=? "
        "ORDER BY position,id",
        (brief_id,),
    ):
        terms[str(kind)].append((str(term), _array(aliases)))
    credentials = [
        (str(term), _array(aliases))
        for term, aliases in connection.exec_driver_sql(
            "SELECT term,aliases FROM brief_credential WHERE brief_id=? "
            "ORDER BY position,id",
            (brief_id,),
        )
    ]
    return build_manifest(
        required_skills=skills["required"],
        optional_skills=skills["optional"],
        target_titles=terms["target_title"],
        industries=terms["industry"],
        location=location,
        required_credentials=credentials,
    )


def _prepare_brief_manifests(connection: Connection) -> None:
    columns = {
        str(row[1])
        for row in connection.exec_driver_sql("PRAGMA table_info(role_brief)")
    }
    connection.exec_driver_sql("DROP TRIGGER IF EXISTS role_brief_append_only")
    if "scoring_inputs" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE role_brief ADD COLUMN scoring_inputs JSON"
        )
    for brief_id, location, stored in connection.exec_driver_sql(
        "SELECT id,location,scoring_inputs FROM role_brief"
    ):
        expected = _brief_manifest(connection, str(brief_id), str(location))
        if stored is None:
            connection.exec_driver_sql(
                "UPDATE role_brief SET scoring_inputs=? WHERE id=?",
                (
                    json.dumps(
                        expected,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    str(brief_id),
                ),
            )
            continue
        try:
            actual = json.loads(stored) if isinstance(stored, str) else stored
        except (TypeError, json.JSONDecodeError) as error:
            raise RuntimeError(
                f"cannot apply {VERSION}: brief {brief_id} has invalid scoring inputs"
            ) from error
        if actual != expected:
            raise RuntimeError(
                f"cannot apply {VERSION}: brief {brief_id} scoring inputs do not "
                "match its immutable terms"
            )


def _preflight(connection: Connection) -> None:
    duplicate = connection.exec_driver_sql(
        "SELECT score_id,signal_id FROM score_signal "
        "GROUP BY score_id,signal_id HAVING count(*)>1 LIMIT 1"
    ).first()
    if duplicate is not None:
        raise RuntimeError(
            f"cannot apply {VERSION}: score {duplicate[0]} has duplicate "
            f"signal {duplicate[1]}"
        )
    polarity = connection.exec_driver_sql(
        "SELECT claim.id FROM score_claim claim JOIN evidence e "
        "ON e.evidence_set_id=claim.evidence_set_id "
        "WHERE (claim.verdict='matched' AND e.polarity<>'supporting') "
        "OR (claim.verdict='contradicted' AND e.polarity<>'contradicting') "
        "LIMIT 1"
    ).first()
    if polarity is not None:
        raise RuntimeError(
            f"cannot apply {VERSION}: claim {polarity[0]} has incompatible polarity"
        )
    signal = connection.exec_driver_sql(
        f"""SELECT s.id FROM score s JOIN scoring_config cfg
        ON cfg.id=s.scoring_config_id
        WHERE (s.is_current=1 OR s.superseded_at IS NOT NULL)
          AND ({_FINALIZED_SIGNAL_INVALID}) LIMIT 1"""
    ).first()
    if signal is not None:
        raise RuntimeError(
            f"cannot apply {VERSION}: finalized score {signal[0]} has an invalid "
            "signal set or weight"
        )
    coverage = connection.exec_driver_sql(
        f"""SELECT claim.id FROM score_claim claim
        JOIN score_signal ss ON ss.id=claim.score_signal_id
        JOIN score s ON s.id=ss.score_id
        JOIN role_brief rb ON rb.id=s.brief_id
        JOIN coverage_set cs ON cs.id=claim.coverage_set_id
        WHERE claim.verdict='not_matched' AND (
          ({_BRIEF_MANIFEST_INVALID})
          OR NOT {_CLAIM_IS_BRIEF_BOUND}
          OR NOT EXISTS (SELECT 1 FROM signal_coverage coverage
                         WHERE coverage.coverage_set_id=cs.id)
          OR EXISTS (SELECT 1 FROM signal_coverage coverage
            WHERE coverage.coverage_set_id=cs.id AND (
              {_COVERAGE_SHAPE_INVALID}
              OR json(coverage.normalized_terms)<>json({_EXPECTED_TERMS})
              OR json(coverage.aliases)<>json({_EXPECTED_ALIASES}))))
        LIMIT 1"""
    ).first()
    if coverage is not None:
        raise RuntimeError(
            f"cannot apply {VERSION}: claim {coverage[0]} has noncanonical "
            "absence coverage"
        )


def apply(connection: Connection) -> None:
    _prepare_brief_manifests(connection)
    _preflight(connection)
    for statement in STATEMENTS:
        connection.exec_driver_sql(statement)
