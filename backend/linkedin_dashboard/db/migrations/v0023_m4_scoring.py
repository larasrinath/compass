"""Add immutable M4 scoring, provenance, and phase-gate storage."""

# ruff: noqa: E501 -- keeping each SQLite trigger clause visible aids review.

from __future__ import annotations

import hashlib
from typing import cast

from sqlalchemy import Connection, Table

from linkedin_dashboard.db.models import (
    BriefCredential,
    CandidateScore,
    CoverageSetRecord,
    Evidence,
    EvidenceSetRecord,
    MissingSetRecord,
    PhaseGate,
    PhaseGateEvidence,
    ProfileSection,
    RoleBrief,
    ScoreClaim,
    ScoreInputSection,
    ScorePenalty,
    ScoreSignal,
    ScoringConfig,
    SignalCoverage,
    SignalMissingSection,
)

VERSION = "0023_m4_scoring"

_SCORING_TABLES = (
    "phase_gate_evidence",
    "score_claim",
    "signal_coverage",
    "signal_missing_section",
    "score_penalty",
    "score_input_section",
    "evidence_set",
    "coverage_set",
    "missing_set",
    "scoring_config",
    "brief_credential",
)


def _columns(connection: Connection, table: str) -> set[str]:
    return {
        str(row[1])
        for row in connection.exec_driver_sql(f'PRAGMA table_xinfo("{table}")').all()
    }


def _drop_table_objects(connection: Connection, table: str) -> list[str]:
    rows = connection.exec_driver_sql(
        "SELECT type,name,sql FROM sqlite_master WHERE tbl_name=? "
        "AND type IN ('trigger','index') AND name NOT LIKE 'sqlite_%'",
        (table,),
    ).all()
    definitions: list[str] = []
    for kind, name, sql in rows:
        if isinstance(sql, str):
            definitions.append(sql)
        quoted = str(name).replace('"', '""')
        connection.exec_driver_sql(f'DROP {str(kind).upper()} "{quoted}"')
    return definitions


def _rebuild(
    connection: Connection,
    table: str,
    model: Table,
    expressions: dict[str, str] | None = None,
) -> None:
    expressions = expressions or {}
    old_columns = _columns(connection, table)
    target_columns = [column.name for column in model.columns]
    select_values = [
        expressions.get(name, f'legacy."{name}"' if name in old_columns else "NULL")
        for name in target_columns
    ]
    old = f"__{VERSION}_{table}"
    definitions = _drop_table_objects(connection, table)
    connection.exec_driver_sql("PRAGMA legacy_alter_table=ON")
    connection.exec_driver_sql(f'ALTER TABLE "{table}" RENAME TO "{old}"')
    model.create(connection)
    connection.exec_driver_sql(
        f'INSERT INTO "{table}" ({", ".join(target_columns)}) '
        f'SELECT {", ".join(select_values)} FROM "{old}" AS legacy'
    )
    connection.exec_driver_sql(f'DROP TABLE "{old}"')
    connection.exec_driver_sql("PRAGMA legacy_alter_table=OFF")
    for definition in definitions:
        connection.exec_driver_sql(definition)


def _create_new_tables(connection: Connection) -> None:
    for model in (
        BriefCredential,
        ScoringConfig,
        EvidenceSetRecord,
        CoverageSetRecord,
        MissingSetRecord,
        ScoreInputSection,
        SignalCoverage,
        SignalMissingSection,
        ScoreClaim,
        ScorePenalty,
        PhaseGateEvidence,
    ):
        cast(Table, model.__table__).create(connection, checkfirst=True)


def _canonical_rebuilds(connection: Connection) -> None:
    _rebuild(
        connection,
        "role_brief",
        cast(Table, RoleBrief.__table__),
        {"required_experience_months": "NULL"},
    )
    _rebuild(
        connection,
        "phase_gate",
        cast(Table, PhaseGate.__table__),
        {"evidence_manifest": "'[]'"},
    )

    section_hashes = {
        str(row[0]): hashlib.sha256(str(row[1]).encode("utf-8")).hexdigest()
        for row in connection.exec_driver_sql(
            "SELECT id,raw_text FROM profile_section"
        ).all()
    }
    if "content_sha256" not in _columns(connection, "profile_section"):
        connection.exec_driver_sql(
            "ALTER TABLE profile_section ADD COLUMN content_sha256 VARCHAR(64)"
        )
    for section_id, digest in section_hashes.items():
        connection.exec_driver_sql(
            "UPDATE profile_section SET content_sha256=? WHERE id=?",
            (digest, section_id),
        )
    _rebuild(connection, "profile_section", cast(Table, ProfileSection.__table__))

    _rebuild(
        connection,
        "score",
        cast(Table, CandidateScore.__table__),
        {
            "scoring_config_id": "NULL",
            "calculation_status": "'scored'",
            "active_signal_count": (
                "(SELECT count(*) FROM score_signal WHERE score_id=legacy.id)"
            ),
            "all_inert_attested": "0",
            "input_fingerprint": "'legacy-' || legacy.id",
            "source_snapshot": "'{}'",
        },
    )
    _rebuild(
        connection,
        "score_signal",
        cast(Table, ScoreSignal.__table__),
        {
            "rollup": (
                "CASE WHEN legacy.verdict='partial' THEN 'mixed' "
                "ELSE legacy.verdict END"
            )
        },
    )
    _rebuild(connection, "evidence", cast(Table, Evidence.__table__))


_ACTIVE_BRIEF = """
SELECT rb.id FROM role_brief rb
WHERE rb.session_id=NEW.session_id AND rb.superseded_at IS NULL
  AND (
    EXISTS (SELECT 1 FROM brief_skill bs WHERE bs.brief_id=rb.id)
    OR coalesce(rb.required_experience_months,0)>0
    OR EXISTS (SELECT 1 FROM brief_term bt WHERE bt.brief_id=rb.id)
    OR length(trim(rb.location))>0
    OR EXISTS (SELECT 1 FROM brief_credential bc WHERE bc.brief_id=rb.id)
  )
LIMIT 1
"""


STATEMENTS = (
    "DROP TRIGGER IF EXISTS approved_draft_claim_update",
    """CREATE TRIGGER approved_draft_claim_update
       BEFORE UPDATE ON draft_claim FOR EACH ROW
       WHEN (
         EXISTS (SELECT 1 FROM send_confirmation WHERE draft_id=OLD.draft_id)
         OR EXISTS (SELECT 1 FROM send_attempt WHERE draft_id=OLD.draft_id)
         OR EXISTS (SELECT 1 FROM send_confirmation WHERE draft_id=NEW.draft_id)
         OR EXISTS (SELECT 1 FROM send_attempt WHERE draft_id=NEW.draft_id)
       ) AND EXISTS (
         SELECT 1 FROM message_draft md
         JOIN candidate c ON c.id=md.candidate_id
         JOIN session s ON s.id=c.session_id
         WHERE md.id=OLD.draft_id
       )
       BEGIN SELECT RAISE(ABORT, 'approved draft_claim is immutable'); END""",
    """CREATE UNIQUE INDEX IF NOT EXISTS one_current_score_per_candidate
       ON score(candidate_id) WHERE is_current=1""",
    """CREATE UNIQUE INDEX IF NOT EXISTS one_current_scoring_config_per_session
       ON scoring_config(session_id) WHERE superseded_at IS NULL""",
    """CREATE TRIGGER score_m4_roots_insert BEFORE INSERT ON score
       FOR EACH ROW WHEN NEW.scoring_config_id IS NOT NULL AND NOT EXISTS (
         SELECT 1 FROM candidate c JOIN role_brief rb
           ON rb.id=NEW.brief_id AND rb.session_id=c.session_id
         JOIN scoring_config sc ON sc.id=NEW.scoring_config_id
           AND sc.session_id=c.session_id
         WHERE c.id=NEW.candidate_id
           AND NEW.weights_version=cast(sc.version AS TEXT)
           AND length(NEW.input_fingerprint)=64)
       BEGIN SELECT RAISE(ABORT, 'score roots must share a session'); END""",
    """CREATE TRIGGER scoring_config_shape_insert BEFORE INSERT ON scoring_config
       FOR EACH ROW WHEN json_type(NEW.weights)<>'object'
         OR (SELECT count(*) FROM json_each(NEW.weights))<>7
         OR EXISTS (SELECT 1 FROM json_each(NEW.weights) item
              WHERE item.key NOT IN ('S-1','S-2','S-3','S-4','S-5','S-6','S-8')
                 OR item.type NOT IN ('integer','real') OR item.value<0
                 OR item.value>1000000)
       BEGIN SELECT RAISE(ABORT, 'invalid scoring weights'); END""",
    """CREATE TRIGGER scoring_config_insert_collision
       BEFORE INSERT ON scoring_config FOR EACH ROW WHEN EXISTS (
         SELECT 1 FROM scoring_config old WHERE old.id=NEW.id
           OR (old.session_id=NEW.session_id AND old.version=NEW.version))
       BEGIN SELECT RAISE(ABORT, 'scoring config version already exists'); END""",
    """CREATE TRIGGER scoring_config_s8_insert BEFORE INSERT ON scoring_config
       FOR EACH ROW WHEN coalesce(json_extract(NEW.weights,'$."S-8"'),0)>0
         AND NOT EXISTS (SELECT 1 FROM role_brief rb
           JOIN brief_credential bc ON bc.brief_id=rb.id
           WHERE rb.session_id=NEW.session_id AND rb.superseded_at IS NULL)
       BEGIN SELECT RAISE(ABORT, 'S-8 requires a current credential'); END""",
    f"""CREATE TRIGGER scoring_config_effective_weight_insert
       BEFORE INSERT ON scoring_config FOR EACH ROW
       WHEN EXISTS ({_ACTIVE_BRIEF}) AND NOT EXISTS (
         SELECT 1 FROM role_brief rb WHERE rb.session_id=NEW.session_id
           AND rb.superseded_at IS NULL AND (
             (EXISTS (SELECT 1 FROM brief_skill bs WHERE bs.brief_id=rb.id
                       AND bs.kind='required')
                AND json_extract(NEW.weights,'$."S-1"')>0)
             OR (EXISTS (SELECT 1 FROM brief_skill bs WHERE bs.brief_id=rb.id
                          AND bs.kind='optional')
                AND json_extract(NEW.weights,'$."S-2"')>0)
             OR (coalesce(rb.required_experience_months,0)>0
                AND json_extract(NEW.weights,'$."S-3"')>0)
             OR (EXISTS (SELECT 1 FROM brief_term bt WHERE bt.brief_id=rb.id
                          AND bt.kind='target_title')
                AND json_extract(NEW.weights,'$."S-4"')>0)
             OR (EXISTS (SELECT 1 FROM brief_term bt WHERE bt.brief_id=rb.id
                          AND bt.kind='industry')
                AND json_extract(NEW.weights,'$."S-5"')>0)
             OR (length(trim(rb.location))>0
                AND json_extract(NEW.weights,'$."S-6"')>0)
             OR (EXISTS (SELECT 1 FROM brief_credential bc WHERE bc.brief_id=rb.id)
                AND json_extract(NEW.weights,'$."S-8"')>0)))
       BEGIN SELECT RAISE(ABORT, 'active scoring inputs require positive weight'); END""",
    """CREATE TRIGGER scoring_config_is_immutable BEFORE UPDATE ON scoring_config
       FOR EACH ROW WHEN NEW.id IS NOT OLD.id OR NEW.session_id IS NOT OLD.session_id
         OR NEW.version IS NOT OLD.version OR NEW.created_at IS NOT OLD.created_at
         OR NEW.weights IS NOT OLD.weights
         OR NEW.metro_region_equivalences IS NOT OLD.metro_region_equivalences
         OR NOT (OLD.superseded_at IS NULL AND NEW.superseded_at IS NOT NULL)
       BEGIN SELECT RAISE(ABORT, 'scoring config is immutable'); END""",
    """CREATE TRIGGER scoring_config_no_delete BEFORE DELETE ON scoring_config
       FOR EACH ROW WHEN EXISTS (SELECT 1 FROM session WHERE id=OLD.session_id)
       BEGIN SELECT RAISE(ABORT, 'scoring config is append-only'); END""",
    """CREATE TRIGGER profile_section_hash_is_immutable
       BEFORE UPDATE OF raw_text,content_sha256 ON profile_section FOR EACH ROW
       WHEN NEW.raw_text IS NOT OLD.raw_text OR NEW.content_sha256 IS NOT OLD.content_sha256
       BEGIN SELECT RAISE(ABORT, 'profile section history is immutable'); END""",
    """CREATE TRIGGER score_input_section_exact_insert
       BEFORE INSERT ON score_input_section FOR EACH ROW WHEN NOT EXISTS (
         SELECT 1 FROM score s JOIN profile_section ps
           ON ps.id=NEW.profile_section_id AND ps.candidate_id=s.candidate_id
         WHERE s.id=NEW.score_id AND s.is_current=0 AND s.superseded_at IS NULL
           AND length(NEW.content_sha256)=64
           AND NEW.content_sha256=ps.content_sha256)
       BEGIN SELECT RAISE(ABORT, 'score source section is stale or cross-candidate'); END""",
    """CREATE TRIGGER score_input_section_no_update
       BEFORE UPDATE ON score_input_section FOR EACH ROW
       BEGIN SELECT RAISE(ABORT, 'score source section is immutable'); END""",
    """CREATE TRIGGER score_input_section_no_delete
       BEFORE DELETE ON score_input_section FOR EACH ROW WHEN EXISTS (
         SELECT 1 FROM score s JOIN candidate c ON c.id=s.candidate_id
         JOIN session root ON root.id=c.session_id
         WHERE s.id=OLD.score_id AND s.scoring_config_id IS NOT NULL)
       BEGIN SELECT RAISE(ABORT, 'score source section is append-only'); END""",
    """CREATE TRIGGER score_signal_before_finalize BEFORE INSERT ON score_signal
       FOR EACH ROW WHEN EXISTS (
         SELECT 1 FROM score s WHERE s.id=NEW.score_id
           AND s.scoring_config_id IS NOT NULL
           AND (s.is_current=1 OR s.superseded_at IS NOT NULL))
       BEGIN SELECT RAISE(ABORT, 'current score snapshot is finalized'); END""",
    """CREATE TRIGGER score_signal_m4_shape_insert BEFORE INSERT ON score_signal
       FOR EACH ROW WHEN NEW.rollup IS NOT NULL AND (
         NEW.signal_id NOT IN ('S-1','S-2','S-3','S-4','S-5','S-6','S-8')
         OR NEW.rollup NOT IN ('matched','not_matched','unknown','contradicted','mixed')
         OR NEW.weight<0 OR NEW.availability<0 OR NEW.availability>1
         OR NEW.raw_subscore<0 OR NEW.raw_subscore>1)
       BEGIN SELECT RAISE(ABORT, 'invalid M4 score signal'); END""",
    """CREATE TRIGGER evidence_exact_span_insert BEFORE INSERT ON evidence
       FOR EACH ROW WHEN NEW.evidence_set_id IS NOT NULL AND NOT EXISTS (
         SELECT 1 FROM evidence_set es
         JOIN score_signal ss ON ss.id=NEW.score_signal_id
         JOIN score s ON s.id=ss.score_id AND s.candidate_id=es.candidate_id
         JOIN profile_section ps ON ps.id=NEW.profile_section_id
            AND ps.candidate_id=es.candidate_id
         WHERE es.id=NEW.evidence_set_id
           AND NEW.section_name=ps.section_name
           AND NEW.content_sha256=ps.content_sha256
           AND NEW.span_start>=0 AND NEW.span_end>NEW.span_start
           AND NEW.span_end<=length(ps.raw_text)
           AND substr(ps.raw_text,NEW.span_start+1,
                      NEW.span_end-NEW.span_start)=NEW.snippet
           AND hex(substr(ps.raw_text,NEW.span_start+1,
                          NEW.span_end-NEW.span_start))=hex(NEW.snippet))
       BEGIN SELECT RAISE(ABORT, 'evidence must be an exact same-candidate span'); END""",
    """CREATE TRIGGER evidence_before_finalize BEFORE INSERT ON evidence
       FOR EACH ROW WHEN NEW.evidence_set_id IS NOT NULL AND (
         EXISTS (SELECT 1 FROM score_claim claim
                 WHERE claim.evidence_set_id=NEW.evidence_set_id)
         OR EXISTS (
           SELECT 1 FROM score_signal ss JOIN score s ON s.id=ss.score_id
           WHERE ss.id=NEW.score_signal_id
             AND (s.is_current=1 OR s.superseded_at IS NOT NULL)))
       BEGIN SELECT RAISE(ABORT, 'current score snapshot is finalized'); END""",
    """CREATE TRIGGER signal_coverage_exact_insert BEFORE INSERT ON signal_coverage
       FOR EACH ROW WHEN EXISTS (
         SELECT 1 FROM score_claim claim
         WHERE claim.coverage_set_id=NEW.coverage_set_id)
       OR NOT EXISTS (
         SELECT 1 FROM coverage_set cs JOIN profile_section ps
           ON ps.id=NEW.profile_section_id AND ps.candidate_id=cs.candidate_id
         WHERE cs.id=NEW.coverage_set_id AND NEW.content_sha256=ps.content_sha256
           AND EXISTS (SELECT 1 FROM json_each(cs.required_sections) req
                       WHERE req.value=ps.section_name))
       BEGIN SELECT RAISE(ABORT, 'coverage must use exact completed sections'); END""",
    """CREATE TRIGGER signal_coverage_no_update BEFORE UPDATE ON signal_coverage
       FOR EACH ROW BEGIN SELECT RAISE(ABORT, 'coverage is immutable'); END""",
    """CREATE TRIGGER signal_coverage_no_delete BEFORE DELETE ON signal_coverage
       FOR EACH ROW WHEN EXISTS (
         SELECT 1 FROM score_claim claim JOIN score_signal ss
           ON ss.id=claim.score_signal_id JOIN score s ON s.id=ss.score_id
         JOIN candidate c ON c.id=s.candidate_id
         JOIN session root ON root.id=c.session_id
         WHERE claim.coverage_set_id=OLD.coverage_set_id)
       BEGIN SELECT RAISE(ABORT, 'coverage is append-only'); END""",
    """CREATE TRIGGER signal_missing_exact_insert
       BEFORE INSERT ON signal_missing_section FOR EACH ROW WHEN
         EXISTS (SELECT 1 FROM score_claim claim
                 WHERE claim.missing_set_id=NEW.missing_set_id)
         OR (NEW.section_error_id IS NOT NULL AND NOT EXISTS (
           SELECT 1 FROM missing_set ms JOIN section_error se
             ON se.id=NEW.section_error_id AND se.candidate_id=ms.candidate_id
           WHERE ms.id=NEW.missing_set_id AND se.section_name=NEW.section_name))
         OR (NEW.reason IN ('rate_limit','fetch_error') AND NEW.section_error_id IS NULL)
       BEGIN SELECT RAISE(ABORT, 'missing provenance has invalid lineage'); END""",
    """CREATE TRIGGER evidence_set_is_immutable BEFORE UPDATE ON evidence_set
       FOR EACH ROW BEGIN SELECT RAISE(ABORT, 'evidence set is immutable'); END""",
    """CREATE TRIGGER evidence_set_no_delete BEFORE DELETE ON evidence_set
       FOR EACH ROW WHEN EXISTS (
         SELECT 1 FROM candidate c JOIN session root ON root.id=c.session_id
         WHERE c.id=OLD.candidate_id)
       BEGIN SELECT RAISE(ABORT, 'evidence set is append-only'); END""",
    """CREATE TRIGGER coverage_set_is_immutable BEFORE UPDATE ON coverage_set
       FOR EACH ROW BEGIN SELECT RAISE(ABORT, 'coverage set is immutable'); END""",
    """CREATE TRIGGER coverage_set_no_delete BEFORE DELETE ON coverage_set
       FOR EACH ROW WHEN EXISTS (
         SELECT 1 FROM candidate c JOIN session root ON root.id=c.session_id
         WHERE c.id=OLD.candidate_id)
       BEGIN SELECT RAISE(ABORT, 'coverage set is append-only'); END""",
    """CREATE TRIGGER missing_set_is_immutable BEFORE UPDATE ON missing_set
       FOR EACH ROW BEGIN SELECT RAISE(ABORT, 'missing set is immutable'); END""",
    """CREATE TRIGGER missing_set_no_delete BEFORE DELETE ON missing_set
       FOR EACH ROW WHEN EXISTS (
         SELECT 1 FROM candidate c JOIN session root ON root.id=c.session_id
         WHERE c.id=OLD.candidate_id)
       BEGIN SELECT RAISE(ABORT, 'missing set is append-only'); END""",
    """CREATE TRIGGER signal_missing_no_update
       BEFORE UPDATE ON signal_missing_section FOR EACH ROW
       BEGIN SELECT RAISE(ABORT, 'missing provenance is immutable'); END""",
    """CREATE TRIGGER signal_missing_no_delete
       BEFORE DELETE ON signal_missing_section FOR EACH ROW WHEN EXISTS (
         SELECT 1 FROM score_claim claim JOIN score_signal ss
           ON ss.id=claim.score_signal_id JOIN score s ON s.id=ss.score_id
         JOIN candidate c ON c.id=s.candidate_id
         JOIN session root ON root.id=c.session_id
         WHERE claim.missing_set_id=OLD.missing_set_id)
       BEGIN SELECT RAISE(ABORT, 'missing provenance is append-only'); END""",
    """CREATE TRIGGER score_claim_complete_insert BEFORE INSERT ON score_claim
       FOR EACH ROW WHEN EXISTS (
         SELECT 1 FROM score_signal parent JOIN score s ON s.id=parent.score_id
         WHERE parent.id=NEW.score_signal_id AND s.scoring_config_id IS NOT NULL
           AND (s.is_current=1 OR s.superseded_at IS NOT NULL))
       OR NOT EXISTS (
         SELECT 1 FROM score_signal ss JOIN score s ON s.id=ss.score_id
         WHERE ss.id=NEW.score_signal_id AND (
           (NEW.verdict IN ('matched','contradicted') AND EXISTS (
             SELECT 1 FROM evidence_set es JOIN evidence e
               ON e.evidence_set_id=es.id
             WHERE es.id=NEW.evidence_set_id AND es.candidate_id=s.candidate_id
               AND NOT EXISTS (
                 SELECT 1 FROM evidence owned
                 WHERE owned.evidence_set_id=es.id AND NOT EXISTS (
                   SELECT 1 FROM score_input_section source
                   WHERE source.score_id=s.id
                     AND source.profile_section_id=owned.profile_section_id
                     AND source.content_sha256=owned.content_sha256))
               AND ((NEW.verdict='matched' AND e.polarity='supporting')
                 OR (NEW.verdict='contradicted' AND e.polarity='contradicting'))
             GROUP BY es.id HAVING count(*)>0 AND count(DISTINCT e.polarity)=1))
           OR (NEW.verdict='not_matched' AND EXISTS (
             SELECT 1 FROM coverage_set cs WHERE cs.id=NEW.coverage_set_id
               AND cs.candidate_id=s.candidate_id
               AND json_array_length(cs.required_sections)>0
               AND NOT EXISTS (
                 SELECT 1 FROM signal_coverage owned
                 WHERE owned.coverage_set_id=cs.id AND NOT EXISTS (
                   SELECT 1 FROM score_input_section source
                   WHERE source.score_id=s.id
                     AND source.profile_section_id=owned.profile_section_id
                     AND source.content_sha256=owned.content_sha256))
               AND (SELECT count(DISTINCT value) FROM json_each(cs.required_sections))=
                   (SELECT count(DISTINCT ps.section_name)
                    FROM signal_coverage sc JOIN profile_section ps
                      ON ps.id=sc.profile_section_id
                    WHERE sc.coverage_set_id=cs.id)))
           OR (NEW.verdict='unknown' AND EXISTS (
             SELECT 1 FROM missing_set ms JOIN signal_missing_section sm
               ON sm.missing_set_id=ms.id
             WHERE ms.id=NEW.missing_set_id AND ms.candidate_id=s.candidate_id
             GROUP BY ms.id HAVING count(*)>0))))
       BEGIN SELECT RAISE(ABORT, 'score claim provenance is incomplete'); END""",
    """CREATE TRIGGER score_penalty_before_finalize BEFORE INSERT ON score_penalty
       FOR EACH ROW WHEN EXISTS (
         SELECT 1 FROM score s WHERE s.id=NEW.score_id
           AND (s.is_current=1 OR s.superseded_at IS NOT NULL))
       BEGIN SELECT RAISE(ABORT, 'current score snapshot is finalized'); END""",
    """CREATE TRIGGER score_penalty_is_immutable BEFORE UPDATE ON score_penalty
       FOR EACH ROW BEGIN SELECT RAISE(ABORT, 'score penalty is immutable'); END""",
    """CREATE TRIGGER score_penalty_no_delete BEFORE DELETE ON score_penalty
       FOR EACH ROW WHEN EXISTS (
         SELECT 1 FROM score s JOIN candidate c ON c.id=s.candidate_id
         JOIN session root ON root.id=c.session_id WHERE s.id=OLD.score_id)
       BEGIN SELECT RAISE(ABORT, 'score penalty is append-only'); END""",
    """CREATE TRIGGER score_finalize_current BEFORE UPDATE OF is_current ON score
       FOR EACH ROW WHEN OLD.is_current=0 AND NEW.is_current=1 AND NOT (
         NEW.scoring_config_id IS NOT NULL AND length(NEW.input_fingerprint)=64 AND
         ((NEW.all_inert_attested=1 AND NEW.active_signal_count=0
           AND NEW.calculation_status='unknown' AND NEW.score IS NULL
           AND NEW.score_lower IS NULL AND NEW.score_upper IS NULL
           AND NEW.confidence=0 AND NEW.confidence_band='low'
           AND NOT EXISTS (SELECT 1 FROM score_signal WHERE score_id=NEW.id)
           AND NOT EXISTS (SELECT 1 FROM score_input_section WHERE score_id=NEW.id)
           AND NOT EXISTS (SELECT 1 FROM score_penalty WHERE score_id=NEW.id)
           AND NOT EXISTS (
             SELECT 1 FROM role_brief rb WHERE rb.id=NEW.brief_id AND (
               EXISTS (SELECT 1 FROM brief_skill bs WHERE bs.brief_id=rb.id)
               OR coalesce(rb.required_experience_months,0)>0
               OR EXISTS (SELECT 1 FROM brief_term bt WHERE bt.brief_id=rb.id)
               OR length(trim(rb.location))>0
               OR EXISTS (SELECT 1 FROM brief_credential bc
                          WHERE bc.brief_id=rb.id))))
          OR (NEW.all_inert_attested=0 AND NEW.active_signal_count>0
           AND (SELECT count(*) FROM score_signal WHERE score_id=NEW.id)=
               NEW.active_signal_count
           AND NOT EXISTS (SELECT 1 FROM score_signal ss WHERE ss.score_id=NEW.id
             AND (NOT EXISTS (SELECT 1 FROM score_claim sc
                              WHERE sc.score_signal_id=ss.id)
               OR ss.rollup IS NOT (SELECT CASE WHEN count(DISTINCT sc.verdict)=1
                    THEN min(sc.verdict) ELSE 'mixed' END FROM score_claim sc
                    WHERE sc.score_signal_id=ss.id)))
           AND ((NEW.calculation_status='unknown' AND NEW.score IS NULL
                 AND NEW.score_lower IS NULL AND NEW.score_upper IS NULL
                 AND NEW.confidence=0 AND NEW.confidence_band IS NULL)
             OR (NEW.calculation_status='scored' AND NEW.score IS NOT NULL
                 AND NEW.score_lower BETWEEN 0 AND NEW.score
                 AND NEW.score_upper BETWEEN NEW.score AND 100
                 AND NEW.confidence>0 AND NEW.confidence<=1
                 AND NEW.confidence_band IN ('low','medium','high'))))))
       BEGIN SELECT RAISE(ABORT, 'current score is incomplete or inconsistent'); END""",
    """CREATE TRIGGER score_content_is_immutable BEFORE UPDATE ON score
       FOR EACH ROW WHEN OLD.scoring_config_id IS NOT NULL AND (
         NEW.id IS NOT OLD.id OR NEW.candidate_id IS NOT OLD.candidate_id
         OR NEW.brief_id IS NOT OLD.brief_id
         OR NEW.scoring_config_id IS NOT OLD.scoring_config_id
         OR NEW.weights_version IS NOT OLD.weights_version OR NEW.stage IS NOT OLD.stage
         OR NEW.score IS NOT OLD.score OR NEW.score_lower IS NOT OLD.score_lower
         OR NEW.score_upper IS NOT OLD.score_upper OR NEW.confidence IS NOT OLD.confidence
         OR NEW.confidence_band IS NOT OLD.confidence_band
         OR NEW.calculation_status IS NOT OLD.calculation_status
         OR NEW.active_signal_count IS NOT OLD.active_signal_count
         OR NEW.all_inert_attested IS NOT OLD.all_inert_attested
         OR NEW.input_fingerprint IS NOT OLD.input_fingerprint
         OR NEW.source_snapshot IS NOT OLD.source_snapshot
         OR NEW.computed_at IS NOT OLD.computed_at
         OR NOT ((OLD.is_current=1 AND NEW.is_current=0
                  AND OLD.superseded_at IS NULL AND NEW.superseded_at IS NOT NULL)
              OR (OLD.is_current=0 AND NEW.is_current=1
                  AND OLD.superseded_at IS NEW.superseded_at)
              OR (OLD.is_current IS NEW.is_current
                  AND OLD.superseded_at IS NEW.superseded_at)))
       BEGIN SELECT RAISE(ABORT, 'score identity is immutable'); END""",
    """CREATE TRIGGER score_m4_insert_collision BEFORE INSERT ON score
       FOR EACH ROW WHEN EXISTS (
         SELECT 1 FROM score old WHERE old.scoring_config_id IS NOT NULL
           AND (old.id=NEW.id
             OR (NEW.is_current=1 AND old.is_current=1
                 AND old.candidate_id=NEW.candidate_id)))
       BEGIN SELECT RAISE(ABORT, 'M4 score identity already exists'); END""",
    """CREATE TRIGGER score_m4_no_delete BEFORE DELETE ON score
       FOR EACH ROW WHEN OLD.scoring_config_id IS NOT NULL AND EXISTS (
         SELECT 1 FROM candidate c JOIN session s ON s.id=c.session_id
         WHERE c.id=OLD.candidate_id)
       BEGIN SELECT RAISE(ABORT, 'M4 score is append-only'); END""",
    """CREATE TRIGGER score_signal_m4_insert_collision
       BEFORE INSERT ON score_signal FOR EACH ROW WHEN EXISTS (
         SELECT 1 FROM score_signal old JOIN score s ON s.id=old.score_id
         WHERE old.id=NEW.id AND s.scoring_config_id IS NOT NULL)
       BEGIN SELECT RAISE(ABORT, 'M4 score signal already exists'); END""",
    """CREATE TRIGGER score_child_is_immutable BEFORE UPDATE ON score_signal
       FOR EACH ROW WHEN OLD.rollup IS NOT NULL
       BEGIN SELECT RAISE(ABORT, 'score signal is immutable'); END""",
    """CREATE TRIGGER score_signal_m4_no_delete BEFORE DELETE ON score_signal
       FOR EACH ROW WHEN EXISTS (
         SELECT 1 FROM score s JOIN candidate c ON c.id=s.candidate_id
         JOIN session root ON root.id=c.session_id
         WHERE s.id=OLD.score_id AND s.scoring_config_id IS NOT NULL)
       BEGIN SELECT RAISE(ABORT, 'M4 score signal is append-only'); END""",
    """CREATE TRIGGER score_claim_insert_collision BEFORE INSERT ON score_claim
       FOR EACH ROW WHEN EXISTS (SELECT 1 FROM score_claim old WHERE old.id=NEW.id)
       BEGIN SELECT RAISE(ABORT, 'score claim already exists'); END""",
    """CREATE TRIGGER score_claim_is_immutable BEFORE UPDATE ON score_claim
       FOR EACH ROW BEGIN SELECT RAISE(ABORT, 'score claim is immutable'); END""",
    """CREATE TRIGGER score_claim_no_delete BEFORE DELETE ON score_claim
       FOR EACH ROW WHEN EXISTS (
         SELECT 1 FROM score_signal ss JOIN score s ON s.id=ss.score_id
         JOIN candidate c ON c.id=s.candidate_id
         JOIN session root ON root.id=c.session_id
         WHERE ss.id=OLD.score_signal_id)
       BEGIN SELECT RAISE(ABORT, 'score claim is append-only'); END""",
    """CREATE TRIGGER evidence_m4_insert_collision BEFORE INSERT ON evidence
       FOR EACH ROW WHEN EXISTS (SELECT 1 FROM evidence old WHERE old.id=NEW.id
                                 AND old.evidence_set_id IS NOT NULL)
       BEGIN SELECT RAISE(ABORT, 'score evidence already exists'); END""",
    """CREATE TRIGGER evidence_m4_is_immutable BEFORE UPDATE ON evidence
       FOR EACH ROW WHEN OLD.evidence_set_id IS NOT NULL AND (
         NEW.id IS NOT OLD.id OR NEW.score_signal_id IS NOT OLD.score_signal_id
         OR NEW.evidence_set_id IS NOT OLD.evidence_set_id
         OR NEW.section_name IS NOT OLD.section_name
         OR NEW.profile_section_id IS NOT OLD.profile_section_id
         OR NEW.content_sha256 IS NOT OLD.content_sha256
         OR NEW.span_start IS NOT OLD.span_start OR NEW.span_end IS NOT OLD.span_end
         OR NEW.matcher IS NOT OLD.matcher OR NEW.polarity IS NOT OLD.polarity
         OR ((NEW.snippet IS NOT OLD.snippet
              OR NEW.matched_term IS NOT OLD.matched_term
              OR NEW.parsed_field_id IS NOT OLD.parsed_field_id)
             AND NOT (NEW.snippet='[purged]' AND NEW.matched_term='[purged]'
                      AND NEW.parsed_field_id IS NULL)))
       BEGIN SELECT RAISE(ABORT, 'score evidence is immutable'); END""",
    """CREATE TRIGGER evidence_m4_no_delete BEFORE DELETE ON evidence
       FOR EACH ROW WHEN OLD.evidence_set_id IS NOT NULL AND EXISTS (
         SELECT 1 FROM score_signal ss JOIN score s ON s.id=ss.score_id
         JOIN candidate c ON c.id=s.candidate_id
         JOIN session root ON root.id=c.session_id
         WHERE ss.id=OLD.score_signal_id)
       BEGIN SELECT RAISE(ABORT, 'score evidence is append-only'); END""",
    """CREATE TRIGGER phase_gate_manifest_insert BEFORE INSERT ON phase_gate
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
               JOIN candidate c ON c.id=s.candidate_id
               JOIN profile_section ps ON ps.id=e.profile_section_id
               WHERE e.id=json_extract(item.value,'$.evidence_id')
                 AND s.id=json_extract(item.value,'$.score_id') AND s.is_current=1
                 AND s.input_fingerprint=json_extract(item.value,'$.input_fingerprint')
                 AND c.session_id=NEW.session_id
                 AND ps.content_sha256=e.content_sha256
                 AND substr(ps.raw_text,e.span_start+1,e.span_end-e.span_start)=e.snippet))))
       BEGIN SELECT RAISE(ABORT, 'Gate B requires ten current exact evidence spans'); END""",
    """CREATE TRIGGER phase_gate_evidence_exact_insert
       BEFORE INSERT ON phase_gate_evidence FOR EACH ROW WHEN NOT EXISTS (
         SELECT 1 FROM phase_gate pg JOIN json_each(pg.evidence_manifest) item
         JOIN evidence e ON e.id=NEW.evidence_id JOIN score_signal ss
           ON ss.id=e.score_signal_id JOIN score s ON s.id=ss.score_id
         JOIN candidate c ON c.id=s.candidate_id
         WHERE pg.id=NEW.phase_gate_id AND pg.gate='B'
           AND pg.session_id=c.session_id AND s.is_current=1
           AND NEW.score_id=s.id AND NEW.input_fingerprint=s.input_fingerprint
           AND json_extract(item.value,'$.evidence_id')=NEW.evidence_id
           AND json_extract(item.value,'$.score_id')=NEW.score_id
           AND json_extract(item.value,'$.input_fingerprint')=NEW.input_fingerprint)
       BEGIN SELECT RAISE(ABORT, 'Gate B evidence is stale or cross-session'); END""",
    """CREATE TRIGGER phase_gate_evidence_is_immutable
       BEFORE UPDATE ON phase_gate_evidence FOR EACH ROW
       BEGIN SELECT RAISE(ABORT, 'phase gate evidence is immutable'); END""",
    """CREATE TRIGGER phase_gate_evidence_no_delete
       BEFORE DELETE ON phase_gate_evidence FOR EACH ROW WHEN EXISTS (
         SELECT 1 FROM phase_gate pg JOIN session root ON root.id=pg.session_id
         WHERE pg.id=OLD.phase_gate_id)
       BEGIN SELECT RAISE(ABORT, 'phase gate evidence is append-only'); END""",
    """CREATE TRIGGER phase_gate_is_immutable BEFORE UPDATE ON phase_gate
       FOR EACH ROW BEGIN SELECT RAISE(ABORT, 'phase gate is append-only'); END""",
    """CREATE TRIGGER phase_gate_insert_collision BEFORE INSERT ON phase_gate
       FOR EACH ROW WHEN EXISTS (SELECT 1 FROM phase_gate old
         WHERE old.id=NEW.id OR (old.session_id=NEW.session_id AND old.gate=NEW.gate))
       BEGIN SELECT RAISE(ABORT, 'phase gate already exists'); END""",
    """CREATE TRIGGER phase_gate_no_delete BEFORE DELETE ON phase_gate
       FOR EACH ROW WHEN EXISTS (SELECT 1 FROM session WHERE id=OLD.session_id)
       BEGIN SELECT RAISE(ABORT, 'phase gate is append-only'); END""",
)


def apply(connection: Connection) -> None:
    duplicate = connection.exec_driver_sql(
        "SELECT candidate_id FROM score WHERE is_current=1 "
        "GROUP BY candidate_id HAVING count(*)>1 LIMIT 1"
    ).first()
    if duplicate is not None:
        raise RuntimeError(f"cannot apply {VERSION}: multiple current scores")
    _create_new_tables(connection)
    _canonical_rebuilds(connection)
    for statement in STATEMENTS:
        connection.exec_driver_sql(statement)
