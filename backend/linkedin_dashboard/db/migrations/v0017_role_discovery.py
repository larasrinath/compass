from __future__ import annotations

# DDL is kept visually aligned with SQLite's stored schema text.
# ruff: noqa: E501
import json
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import Connection, Table

from linkedin_dashboard.db.models import (
    BriefSkill,
    BriefTerm,
    Candidate,
    CandidateIdentityMetadata,
    CandidateReference,
    CompanyLookup,
    RoleBrief,
    SearchRun,
)
from linkedin_dashboard.db.unicode_identity import (
    unicode_casefold,
    unicode_data_version,
)

VERSION = "0017_role_discovery"

TRIGGER_NAMES = (
    "role_brief_append_only",
    "role_brief_no_delete",
    "brief_skill_insert_only_while_unsealed",
    "brief_skill_is_immutable",
    "brief_skill_no_delete",
    "brief_term_is_immutable",
    "brief_term_no_delete",
    "brief_term_insert_only_while_unsealed",
    "discovery_job_identity_immutable",
    "discovery_job_no_delete",
    "search_run_owner_insert",
    "search_run_owner_update",
    "search_run_identity_immutable",
    "search_run_result_immutable",
    "search_run_no_delete",
    "candidate_dedupe_insert",
    "candidate_dedupe_duplicate_insert",
    "candidate_dedupe_derive",
    "candidate_dedupe_immutable",
    "candidate_identity_metadata_immutable",
    "candidate_identity_metadata_no_delete",
    "candidate_identity_immutable",
    "candidate_ref_identity_immutable",
    "candidate_ref_insert_only_while_unprocessed",
    "candidate_ref_no_delete",
    "candidate_source_owner_insert",
    "candidate_source_owner_update",
    "candidate_source_is_immutable",
    "candidate_source_no_delete",
    "company_lookup_owner_insert",
    "company_lookup_owner_update",
    "company_lookup_replace_insert",
    "company_lookup_identity_immutable",
    "company_lookup_result_immutable",
    "company_lookup_no_delete",
)

INDEX_NAMES = ("candidate_session_dedupe_key", "candidate_session_profile_url_key")


def _columns(connection: Connection, table: str) -> set[str]:
    # table_xinfo includes generated columns; table_info does not everywhere.
    return {
        str(row[1])
        for row in connection.exec_driver_sql(f'PRAGMA table_xinfo("{table}")').all()
    }


def _decode_json(value: Any) -> Any:
    if value is None or not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"cannot apply {VERSION}: malformed legacy JSON") from error


def _drop_table_objects(connection: Connection, table: str) -> None:
    objects = connection.exec_driver_sql(
        "SELECT type, name FROM sqlite_master "
        "WHERE tbl_name = ? AND type IN ('trigger','index') "
        "AND name NOT LIKE 'sqlite_%'",
        (table,),
    ).all()
    for kind, name in objects:
        quoted = str(name).replace('"', '""')
        connection.exec_driver_sql(f'DROP {str(kind).upper()} "{quoted}"')


def _begin_rebuild(connection: Connection, table: str) -> str:
    old = f"__{VERSION}_{table}"
    _drop_table_objects(connection, table)
    connection.exec_driver_sql("PRAGMA legacy_alter_table = ON")
    connection.exec_driver_sql(f'ALTER TABLE "{table}" RENAME TO "{old}"')
    return old


def _finish_rebuild(connection: Connection, old: str) -> None:
    connection.exec_driver_sql(f'DROP TABLE "{old}"')
    connection.exec_driver_sql("PRAGMA legacy_alter_table = OFF")


def _preflight(connection: Connection) -> None:
    if "candidate_identity_metadata" in {
        str(row[0])
        for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }:
        versions = connection.exec_driver_sql(
            "SELECT id, unicode_version FROM candidate_identity_metadata"
        ).all()
        if versions and versions != [(1, unicode_data_version())]:
            raise RuntimeError(
                f"cannot apply {VERSION}: candidate identity Unicode version mismatch; "
                "an explicit migration and REINDEX are required"
            )
    candidate_columns = _columns(connection, "candidate")
    selected = "session_id, username"
    if "dedupe_key" in candidate_columns:
        selected += ", dedupe_key"
    identities: set[tuple[str, str]] = set()
    for row in connection.exec_driver_sql(f"SELECT {selected} FROM candidate"):
        session_id = str(row[0])
        username = str(row[1])
        key = unicode_casefold(username)
        if len(row) == 3 and str(row[2]) != key:
            raise RuntimeError(
                f"cannot apply {VERSION}: noncanonical candidate dedupe key"
            )
        identity = (session_id, key)
        if identity in identities:
            raise RuntimeError(
                f"cannot apply {VERSION}: duplicate normalized candidate identity"
            )
        identities.add(identity)
    duplicate_url = connection.exec_driver_sql(
        "SELECT 1 FROM candidate GROUP BY session_id, lower(rtrim(profile_url, '/')) "
        "HAVING count(*) > 1 LIMIT 1"
    ).first()
    if duplicate_url is not None:
        raise RuntimeError(
            f"cannot apply {VERSION}: duplicate normalized candidate URL"
        )
    mismatch = connection.exec_driver_sql(
        "SELECT 1 FROM candidate_source AS source "
        "LEFT JOIN candidate AS candidate ON candidate.id = source.candidate_id "
        "LEFT JOIN search_run AS run ON run.id = source.search_run_id "
        "LEFT JOIN candidate_ref AS ref ON ref.id = source.candidate_ref_id "
        "WHERE candidate.id IS NULL OR run.id IS NULL OR ref.id IS NULL "
        "OR candidate.session_id <> run.session_id "
        "OR ref.search_run_id <> source.search_run_id LIMIT 1"
    ).first()
    if mismatch is not None:
        raise RuntimeError(f"cannot apply {VERSION}: inconsistent candidate provenance")
    invalid_json = connection.exec_driver_sql(
        "SELECT 1 FROM role_brief WHERE NOT json_valid(target_titles) "
        "OR NOT json_valid(industries) LIMIT 1"
    ).first()
    if invalid_json is not None:
        raise RuntimeError(f"cannot apply {VERSION}: malformed structured brief data")
    invalid_search = connection.exec_driver_sql(
        "SELECT 1 FROM search_run AS run "
        "LEFT JOIN role_brief AS brief ON brief.id=run.brief_id "
        "WHERE brief.id IS NULL OR brief.session_id<>run.session_id "
        "OR (run.network IS NOT NULL AND NOT json_valid(run.network)) "
        "OR (run.raw_response IS NOT NULL AND NOT json_valid(run.raw_response)) LIMIT 1"
    ).first()
    if invalid_search is not None:
        raise RuntimeError(
            f"cannot apply {VERSION}: inconsistent legacy search history"
        )


def _rebuild_brief_skill(connection: Connection) -> None:
    if "position" in _columns(connection, "brief_skill"):
        return
    old = _begin_rebuild(connection, "brief_skill")
    cast(Table, BriefSkill.__table__).create(connection)
    connection.exec_driver_sql(
        f"""
        INSERT INTO brief_skill (id, brief_id, term, kind, aliases, position)
        SELECT current.id, current.brief_id, current.term, current.kind,
               current.aliases,
               (SELECT count(*) - 1 FROM "{old}" AS earlier
                 WHERE earlier.brief_id = current.brief_id
                   AND earlier.kind = current.kind
                   AND earlier.rowid <= current.rowid)
          FROM "{old}" AS current
        """
    )
    _finish_rebuild(connection, old)


def _rebuild_role_brief(connection: Connection) -> None:
    if "sealed_at" in _columns(connection, "role_brief"):
        return
    old = _begin_rebuild(connection, "role_brief")
    cast(Table, RoleBrief.__table__).create(connection)
    connection.exec_driver_sql(
        f"""
        INSERT INTO role_brief
          (id, session_id, version, created_at, sealed_at, superseded_at,
           job_description, target_titles, location, industries,
           positive_keywords, negative_keywords, message_tone, weights_version)
        SELECT id, session_id, version, created_at, created_at, superseded_at,
               job_description, target_titles, location, industries,
               positive_keywords, negative_keywords, message_tone, weights_version
          FROM "{old}"
        """
    )
    _finish_rebuild(connection, old)


def _rebuild_candidate(connection: Connection) -> None:
    if "dedupe_key" in _columns(connection, "candidate"):
        return
    old = _begin_rebuild(connection, "candidate")
    cast(Table, Candidate.__table__).create(connection)
    rows = connection.exec_driver_sql(
        f"SELECT id, session_id, username, profile_url, display_name, profile_urn, "
        f'first_seen_at, stage, retrieval_status FROM "{old}"'
    ).all()
    for row in rows:
        connection.exec_driver_sql(
            """INSERT INTO candidate
              (id, session_id, username, dedupe_key, profile_url, display_name,
               profile_urn, first_seen_at, stage, retrieval_status)
              VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (*row[:3], unicode_casefold(str(row[2])), *row[3:]),
        )
    _finish_rebuild(connection, old)


def _rebuild_candidate_ref(connection: Connection) -> None:
    if "extra" in _columns(connection, "candidate_ref"):
        return
    old = _begin_rebuild(connection, "candidate_ref")
    cast(Table, CandidateReference.__table__).create(connection)
    connection.exec_driver_sql(
        f"""
        INSERT INTO candidate_ref
          (id, search_run_id, kind, url, text, context, value, extra, position)
        SELECT id, search_run_id, kind, url, text, context, value, json('{{}}'), position
          FROM "{old}"
        """
    )
    _finish_rebuild(connection, old)


def _legacy_search_jobs(connection: Connection) -> dict[str, str]:
    rows = connection.exec_driver_sql(
        "SELECT id, session_id, created_at, keywords, location, network, "
        "current_company, status FROM search_run ORDER BY rowid"
    ).mappings()
    job_ids: dict[str, str] = {}
    for row in rows:
        job_id = str(uuid4())
        job_ids[str(row["id"])] = job_id
        status = str(row["status"])
        payload = {
            "keywords": row["keywords"],
            "location": row["location"],
            "network": _decode_json(row["network"]),
            "current_company": row["current_company"],
            "search_run_id": row["id"],
        }
        connection.exec_driver_sql(
            "INSERT INTO job "
            "(id, session_id, kind, payload, state, attempts, max_attempts, queued_at, "
            "started_at, finished_at, error, correlation_id, claim_token) "
            "VALUES (?, ?, 'search_people', ?, ?, 0, 2, ?, ?, ?, NULL, ?, NULL)",
            (
                job_id,
                row["session_id"],
                json.dumps(payload, separators=(",", ":")),
                "failed" if status == "failed" else "done",
                row["created_at"],
                row["created_at"],
                row["created_at"],
                f"legacy-search-{row['id']}",
            ),
        )
    return job_ids


def _rebuild_search_run(connection: Connection) -> None:
    if "job_id" in _columns(connection, "search_run"):
        return
    job_ids = _legacy_search_jobs(connection)
    legacy_rows = [
        dict(row)
        for row in connection.exec_driver_sql("SELECT * FROM search_run").mappings()
    ]
    old = _begin_rebuild(connection, "search_run")
    cast(Table, SearchRun.__table__).create(connection)
    for row in legacy_rows:
        connection.execute(
            cast(Table, SearchRun.__table__)
            .insert()
            .values(
                id=row["id"],
                session_id=row["session_id"],
                brief_id=row["brief_id"],
                job_id=job_ids[str(row["id"])],
                created_at=row["created_at"],
                keywords=row["keywords"],
                location=row["location"],
                network=_decode_json(row["network"]),
                current_company=row["current_company"],
                result_url=row["result_url"],
                raw_response=_decode_json(row["raw_response"]),
                processed_at=row["created_at"],
                reference_count=row["reference_count"],
                person_reference_count=row["person_reference_count"],
                status=row["status"],
            )
        )
    _finish_rebuild(connection, old)


def _migrate_brief_terms(connection: Connection) -> None:
    for kind, column in (("target_title", "target_titles"), ("industry", "industries")):
        connection.exec_driver_sql(
            f"""
            INSERT OR IGNORE INTO brief_term
              (id, brief_id, kind, term, term_key, aliases, position)
            SELECT lower(hex(randomblob(16))), role_brief.id, ?, item.value,
                   lower(item.value), json('[]'), CAST(item.key AS INTEGER)
              FROM role_brief, json_each(role_brief.{column}) AS item
             WHERE item.type = 'text' AND trim(item.value) <> ''
            """,
            (kind,),
        )


_STATEMENTS = (
    "CREATE UNIQUE INDEX IF NOT EXISTS candidate_session_dedupe_key ON candidate(session_id, dedupe_key)",
    "CREATE UNIQUE INDEX IF NOT EXISTS candidate_session_profile_url_key ON candidate(session_id, lower(rtrim(profile_url, '/')))",
    """CREATE TRIGGER IF NOT EXISTS role_brief_append_only BEFORE UPDATE ON role_brief
    FOR EACH ROW WHEN NEW.id IS NOT OLD.id OR NEW.session_id IS NOT OLD.session_id
      OR NEW.version IS NOT OLD.version OR NEW.created_at IS NOT OLD.created_at
      OR NEW.job_description IS NOT OLD.job_description
      OR NEW.target_titles IS NOT OLD.target_titles OR NEW.location IS NOT OLD.location
      OR NEW.industries IS NOT OLD.industries
      OR NEW.positive_keywords IS NOT OLD.positive_keywords
      OR NEW.negative_keywords IS NOT OLD.negative_keywords
      OR NEW.message_tone IS NOT OLD.message_tone
      OR NEW.weights_version IS NOT OLD.weights_version
      OR (NEW.sealed_at IS NOT OLD.sealed_at AND
          (OLD.sealed_at IS NOT NULL OR NEW.sealed_at IS NULL
           OR NEW.sealed_at IS NOT OLD.created_at))
      OR (NEW.superseded_at IS NOT OLD.superseded_at AND
          (OLD.superseded_at IS NOT NULL OR NEW.superseded_at IS NULL))
    BEGIN SELECT RAISE(ABORT, 'purged evidence role brief identity is immutable; cannot cross a score session; role brief versions are append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS role_brief_no_delete BEFORE DELETE ON role_brief
    FOR EACH ROW WHEN EXISTS (SELECT 1 FROM session WHERE id = OLD.session_id)
    BEGIN SELECT RAISE(ABORT, 'purged evidence role brief history may be deleted only by session purge'); END""",
    """CREATE TRIGGER IF NOT EXISTS brief_skill_insert_only_while_unsealed BEFORE INSERT ON brief_skill
    FOR EACH ROW WHEN EXISTS (SELECT 1 FROM role_brief WHERE id=NEW.brief_id AND sealed_at IS NOT NULL)
    BEGIN SELECT RAISE(ABORT, 'sealed role brief terms are immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS brief_skill_is_immutable BEFORE UPDATE ON brief_skill
    FOR EACH ROW BEGIN SELECT RAISE(ABORT, 'brief skill history is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS brief_skill_no_delete BEFORE DELETE ON brief_skill
    FOR EACH ROW WHEN EXISTS (SELECT 1 FROM role_brief b JOIN session s ON s.id=b.session_id WHERE b.id=OLD.brief_id)
    BEGIN SELECT RAISE(ABORT, 'brief skill history may be deleted only by session purge'); END""",
    """CREATE TRIGGER IF NOT EXISTS brief_term_is_immutable BEFORE UPDATE ON brief_term
    FOR EACH ROW BEGIN SELECT RAISE(ABORT, 'brief term history is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS brief_term_no_delete BEFORE DELETE ON brief_term
    FOR EACH ROW WHEN EXISTS (SELECT 1 FROM role_brief b JOIN session s ON s.id=b.session_id WHERE b.id=OLD.brief_id)
    BEGIN SELECT RAISE(ABORT, 'brief term history may be deleted only by session purge'); END""",
    """CREATE TRIGGER IF NOT EXISTS brief_term_insert_only_while_unsealed BEFORE INSERT ON brief_term
    FOR EACH ROW WHEN EXISTS (SELECT 1 FROM role_brief WHERE id=NEW.brief_id AND sealed_at IS NOT NULL)
    BEGIN SELECT RAISE(ABORT, 'sealed role brief terms are immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS discovery_job_identity_immutable BEFORE UPDATE OF id, session_id, kind, payload ON job
    FOR EACH ROW WHEN EXISTS (SELECT 1 FROM search_run WHERE job_id=OLD.id)
      OR EXISTS (SELECT 1 FROM company_lookup WHERE job_id=OLD.id)
    BEGIN SELECT RAISE(ABORT, 'discovery job identity and payload are immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS discovery_job_no_delete BEFORE DELETE ON job
    FOR EACH ROW WHEN EXISTS (SELECT 1 FROM session WHERE id=OLD.session_id)
      AND (EXISTS (SELECT 1 FROM search_run WHERE job_id=OLD.id)
        OR EXISTS (SELECT 1 FROM company_lookup WHERE job_id=OLD.id))
    BEGIN SELECT RAISE(ABORT, 'discovery jobs may be deleted only by session purge'); END""",
    """CREATE TRIGGER IF NOT EXISTS search_run_owner_insert BEFORE INSERT ON search_run
    FOR EACH ROW WHEN NOT EXISTS (SELECT 1 FROM job j JOIN role_brief b ON b.id=NEW.brief_id
      WHERE j.id=NEW.job_id AND j.session_id=NEW.session_id AND b.session_id=NEW.session_id
        AND j.kind='search_people' AND json_extract(j.payload, '$.search_run_id')=NEW.id)
    BEGIN SELECT RAISE(ABORT, 'search run job, brief, and session ownership mismatch'); END""",
    """CREATE TRIGGER IF NOT EXISTS search_run_owner_update BEFORE UPDATE ON search_run
    FOR EACH ROW WHEN NOT EXISTS (SELECT 1 FROM job j JOIN role_brief b ON b.id=NEW.brief_id
      WHERE j.id=NEW.job_id AND j.session_id=NEW.session_id AND b.session_id=NEW.session_id
        AND j.kind='search_people' AND json_extract(j.payload, '$.search_run_id')=NEW.id)
    BEGIN SELECT RAISE(ABORT, 'search run job, brief, and session ownership mismatch'); END""",
    """CREATE TRIGGER IF NOT EXISTS search_run_identity_immutable BEFORE UPDATE ON search_run
    FOR EACH ROW WHEN NEW.id IS NOT OLD.id OR NEW.session_id IS NOT OLD.session_id
      OR NEW.brief_id IS NOT OLD.brief_id OR NEW.job_id IS NOT OLD.job_id
      OR NEW.created_at IS NOT OLD.created_at OR NEW.keywords IS NOT OLD.keywords
      OR NEW.location IS NOT OLD.location OR NEW.network IS NOT OLD.network
      OR NEW.current_company IS NOT OLD.current_company
    BEGIN SELECT RAISE(ABORT, 'search run identity and parameters are immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS search_run_result_immutable BEFORE UPDATE ON search_run
    FOR EACH ROW WHEN OLD.processed_at IS NOT NULL
      OR (OLD.raw_response IS NOT NULL AND OLD.raw_response <> 'null'
          AND NEW.raw_response IS NOT OLD.raw_response)
    BEGIN SELECT RAISE(ABORT, 'processed search result is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS search_run_no_delete BEFORE DELETE ON search_run
    FOR EACH ROW WHEN EXISTS (SELECT 1 FROM session WHERE id=OLD.session_id)
    BEGIN SELECT RAISE(ABORT, 'search history may be deleted only by session purge'); END""",
    """CREATE TRIGGER IF NOT EXISTS candidate_identity_immutable BEFORE UPDATE ON candidate
    FOR EACH ROW WHEN NEW.id IS NOT OLD.id OR NEW.session_id IS NOT OLD.session_id
      OR NEW.username IS NOT OLD.username OR NEW.profile_url IS NOT OLD.profile_url
    BEGIN SELECT RAISE(ABORT, 'purged evidence; recipient identity is immutable; candidate session is immutable; cannot cross a score session; candidate discovery identity is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS candidate_dedupe_insert BEFORE INSERT ON candidate
    FOR EACH ROW WHEN (NEW.dedupe_key = '' AND NEW.username GLOB '*[^ -~]*')
      OR (NEW.dedupe_key <> ''
          AND NOT (NEW.dedupe_key = NEW.username COLLATE unicode_casefold))
    BEGIN SELECT RAISE(ABORT, 'candidate dedupe key must match username'); END""",
    """CREATE TRIGGER IF NOT EXISTS candidate_dedupe_duplicate_insert BEFORE INSERT ON candidate
    FOR EACH ROW WHEN EXISTS (SELECT 1 FROM candidate c WHERE c.session_id=NEW.session_id
      AND c.id<>NEW.id AND c.dedupe_key=NEW.dedupe_key COLLATE unicode_casefold)
    BEGIN SELECT RAISE(ABORT, 'duplicate normalized candidate identity'); END""",
    """CREATE TRIGGER IF NOT EXISTS candidate_dedupe_derive AFTER INSERT ON candidate
    FOR EACH ROW WHEN NEW.dedupe_key = ''
    BEGIN UPDATE candidate SET dedupe_key=lower(NEW.username) WHERE id=NEW.id; END""",
    """CREATE TRIGGER IF NOT EXISTS candidate_dedupe_immutable BEFORE UPDATE OF dedupe_key ON candidate
    FOR EACH ROW WHEN OLD.dedupe_key <> ''
      OR NOT (NEW.dedupe_key = OLD.username COLLATE unicode_casefold)
    BEGIN SELECT RAISE(ABORT, 'candidate dedupe key is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS candidate_identity_metadata_immutable BEFORE UPDATE ON candidate_identity_metadata
    FOR EACH ROW BEGIN SELECT RAISE(ABORT, 'candidate identity Unicode version is immutable; use an explicit migration and REINDEX'); END""",
    """CREATE TRIGGER IF NOT EXISTS candidate_identity_metadata_no_delete BEFORE DELETE ON candidate_identity_metadata
    FOR EACH ROW BEGIN SELECT RAISE(ABORT, 'candidate identity Unicode version may not be deleted'); END""",
    """CREATE TRIGGER IF NOT EXISTS candidate_ref_identity_immutable BEFORE UPDATE ON candidate_ref
    FOR EACH ROW BEGIN SELECT RAISE(ABORT, 'candidate reference provenance is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS candidate_ref_insert_only_while_unprocessed BEFORE INSERT ON candidate_ref
    FOR EACH ROW WHEN EXISTS (SELECT 1 FROM search_run WHERE id=NEW.search_run_id AND processed_at IS NOT NULL)
    BEGIN SELECT RAISE(ABORT, 'processed search reference set is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS candidate_ref_no_delete BEFORE DELETE ON candidate_ref
    FOR EACH ROW WHEN EXISTS (SELECT 1 FROM search_run r JOIN session s ON s.id=r.session_id WHERE r.id=OLD.search_run_id)
    BEGIN SELECT RAISE(ABORT, 'candidate reference history may be deleted only by session purge'); END""",
    """CREATE TRIGGER IF NOT EXISTS candidate_source_owner_insert BEFORE INSERT ON candidate_source
    FOR EACH ROW WHEN NOT EXISTS (SELECT 1 FROM candidate c JOIN search_run r ON r.id=NEW.search_run_id
      JOIN candidate_ref cr ON cr.id=NEW.candidate_ref_id
      WHERE c.id=NEW.candidate_id AND c.session_id=r.session_id
        AND cr.search_run_id=NEW.search_run_id AND r.processed_at IS NULL)
    BEGIN SELECT RAISE(ABORT, 'candidate source ownership mismatch'); END""",
    """CREATE TRIGGER IF NOT EXISTS candidate_source_owner_update BEFORE UPDATE ON candidate_source
    FOR EACH ROW WHEN NOT EXISTS (SELECT 1 FROM candidate c JOIN search_run r ON r.id=NEW.search_run_id
      JOIN candidate_ref cr ON cr.id=NEW.candidate_ref_id
      WHERE c.id=NEW.candidate_id AND c.session_id=r.session_id AND cr.search_run_id=NEW.search_run_id)
    BEGIN SELECT RAISE(ABORT, 'candidate source ownership mismatch'); END""",
    """CREATE TRIGGER IF NOT EXISTS candidate_source_is_immutable BEFORE UPDATE ON candidate_source
    FOR EACH ROW BEGIN SELECT RAISE(ABORT, 'candidate source provenance is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS candidate_source_no_delete BEFORE DELETE ON candidate_source
    FOR EACH ROW WHEN EXISTS (SELECT 1 FROM search_run r JOIN session s ON s.id=r.session_id WHERE r.id=OLD.search_run_id)
    BEGIN SELECT RAISE(ABORT, 'candidate source history may be deleted only by session purge'); END""",
    """CREATE TRIGGER IF NOT EXISTS company_lookup_owner_insert BEFORE INSERT ON company_lookup
    FOR EACH ROW WHEN NOT EXISTS (SELECT 1 FROM job j WHERE j.id=NEW.job_id
      AND j.session_id=NEW.session_id AND j.kind='get_company_profile'
      AND json_extract(j.payload, '$.company_lookup_id')=NEW.id)
    BEGIN SELECT RAISE(ABORT, 'company lookup job and session ownership mismatch'); END""",
    """CREATE TRIGGER IF NOT EXISTS company_lookup_owner_update BEFORE UPDATE ON company_lookup
    FOR EACH ROW WHEN NOT EXISTS (SELECT 1 FROM job j WHERE j.id=NEW.job_id
      AND j.session_id=NEW.session_id AND j.kind='get_company_profile'
      AND json_extract(j.payload, '$.company_lookup_id')=NEW.id)
    BEGIN SELECT RAISE(ABORT, 'company lookup job and session ownership mismatch'); END""",
    """CREATE TRIGGER IF NOT EXISTS company_lookup_replace_insert BEFORE INSERT ON company_lookup
    FOR EACH ROW WHEN EXISTS (SELECT 1 FROM company_lookup existing JOIN session s
      ON s.id=existing.session_id WHERE existing.id=NEW.id OR existing.job_id=NEW.job_id)
    BEGIN SELECT RAISE(ABORT, 'company lookup history is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS company_lookup_identity_immutable BEFORE UPDATE ON company_lookup
    FOR EACH ROW WHEN NEW.id IS NOT OLD.id OR NEW.session_id IS NOT OLD.session_id
      OR NEW.job_id IS NOT OLD.job_id OR NEW.slug IS NOT OLD.slug OR NEW.created_at IS NOT OLD.created_at
    BEGIN SELECT RAISE(ABORT, 'company lookup identity is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS company_lookup_result_immutable BEFORE UPDATE ON company_lookup
    FOR EACH ROW WHEN OLD.processed_at IS NOT NULL
      OR (OLD.raw_response IS NOT NULL AND OLD.raw_response <> 'null'
          AND NEW.raw_response IS NOT OLD.raw_response)
    BEGIN SELECT RAISE(ABORT, 'processed company lookup is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS company_lookup_no_delete BEFORE DELETE ON company_lookup
    FOR EACH ROW WHEN EXISTS (SELECT 1 FROM session WHERE id=OLD.session_id)
    BEGIN SELECT RAISE(ABORT, 'company lookup history may be deleted only by session purge'); END""",
)


def apply(connection: Connection) -> None:
    _preflight(connection)
    cast(Table, CandidateIdentityMetadata.__table__).create(connection, checkfirst=True)
    connection.exec_driver_sql(
        "INSERT OR IGNORE INTO candidate_identity_metadata (id, unicode_version) VALUES (1, ?)",
        (unicode_data_version(),),
    )
    cast(Table, BriefTerm.__table__).create(connection, checkfirst=True)
    cast(Table, CompanyLookup.__table__).create(connection, checkfirst=True)
    _rebuild_role_brief(connection)
    _rebuild_brief_skill(connection)
    _rebuild_candidate(connection)
    _rebuild_candidate_ref(connection)
    _rebuild_search_run(connection)
    _migrate_brief_terms(connection)
    for statement in _STATEMENTS:
        connection.exec_driver_sql(statement)
