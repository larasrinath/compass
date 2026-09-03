"""Canonicalize M3 storage, identity authority, and navigation reservations."""

from __future__ import annotations

from typing import cast

from sqlalchemy import Connection, Table

from linkedin_dashboard.db.models import (
    Candidate,
    JobAttempt,
    NavigationReservation,
    ProfileFetch,
    SectionError,
    SectionReference,
)

VERSION = "0021_m3_final_integrity"


def _drop_table_objects(connection: Connection, table: str) -> list[str]:
    rows = connection.exec_driver_sql(
        "SELECT type, name, sql FROM sqlite_master WHERE tbl_name=? "
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
    columns: tuple[str, ...],
    expressions: tuple[str, ...],
) -> None:
    old = f"__{VERSION}_{table}"
    definitions = _drop_table_objects(connection, table)
    connection.exec_driver_sql("PRAGMA legacy_alter_table=ON")
    connection.exec_driver_sql(f'ALTER TABLE "{table}" RENAME TO "{old}"')
    model.create(connection)
    connection.exec_driver_sql(
        f'INSERT INTO "{table}" ({", ".join(columns)}) '
        f'SELECT {", ".join(expressions)} FROM "{old}"'
    )
    connection.exec_driver_sql(f'DROP TABLE "{old}"')
    connection.exec_driver_sql("PRAGMA legacy_alter_table=OFF")
    for definition in definitions:
        connection.exec_driver_sql(definition)


def _canonical_rebuilds(connection: Connection) -> None:
    candidate_columns = (
        "id",
        "session_id",
        "username",
        "profile_url",
        "display_name",
        "profile_urn",
        "profile_urn_quarantined",
        "profile_contract_error",
        "first_seen_at",
        "stage",
        "retrieval_status",
    )
    _rebuild(
        connection,
        "candidate",
        cast(Table, Candidate.__table__),
        candidate_columns,
        candidate_columns,
    )
    fetch_columns = (
        "id",
        "candidate_id",
        "job_id",
        "tool",
        "requested_sections",
        "args",
        "started_at",
        "finished_at",
        "duration_ms",
        "outcome",
        "raw_response",
        "projection_payload",
        "projection_source",
        "contract_error",
        "returned_url",
        "processed_at",
        "request_stage",
        "parent_fetch_id",
        "root_fetch_id",
    )
    _rebuild(
        connection,
        "profile_fetch",
        cast(Table, ProfileFetch.__table__),
        fetch_columns,
        fetch_columns,
    )
    attempt_columns = (
        "id",
        "job_id",
        "attempt_number",
        "worker_token",
        "started_at",
        "response_received_at",
        "external_call_started_at",
        "finished_at",
        "outcome",
        "raw_response",
        "raw_error",
        "error_class",
        "safe_error_message",
        "retry_at",
    )
    _rebuild(
        connection,
        "job_attempt",
        cast(Table, JobAttempt.__table__),
        attempt_columns,
        tuple(
            # The pre-v0021 schema recorded no durable pre-executor phase, so
            # no old attempt can prove that navigation was never risked.
            "started_at" if item == "external_call_started_at" else item
            for item in attempt_columns
        ),
    )
    error_columns = (
        "id",
        "candidate_id",
        "search_run_id",
        "fetch_id",
        "section_name",
        "error_type",
        "error_message",
        "extra",
        "source_item",
    )
    _rebuild(
        connection,
        "section_error",
        cast(Table, SectionError.__table__),
        error_columns,
        tuple("NULL" if item == "source_item" else item for item in error_columns),
    )
    reference_columns = (
        "id",
        "candidate_id",
        "section_name",
        "kind",
        "url",
        "text",
        "context",
        "value",
        "fetch_id",
        "source_position",
    )
    _rebuild(
        connection,
        "section_reference",
        cast(Table, SectionReference.__table__),
        reference_columns,
        tuple(
            "NULL" if item == "source_position" else item for item in reference_columns
        ),
    )


def apply(connection: Connection) -> None:
    _canonical_rebuilds(connection)
    cast(Table, NavigationReservation.__table__).create(connection, checkfirst=True)
    connection.exec_driver_sql(
        """INSERT INTO navigation_reservation
          (job_id,session_id,cost,refunded_navigations,state,reserved_at,
           charged_at,released_at)
        SELECT id,session_id,
          CASE kind
            WHEN 'get_person_profile' THEN 1 + (
              SELECT count(*) FROM json_each(job.payload,'$.sections') item
              WHERE item.value<>'main_profile')
            WHEN 'get_company_profile' THEN CASE
              WHEN json_type(payload,'$.sections')='array'
                THEN json_array_length(payload,'$.sections') ELSE 1 END
            WHEN 'search_people' THEN 1 ELSE 0 END,
          0, CASE WHEN attempts=0 THEN 'reserved' ELSE 'charged' END,
          queued_at, CASE WHEN attempts=0 THEN NULL ELSE started_at END, NULL
        FROM job WHERE state IN ('pending','queued','running')
          AND kind<>'list_tools'
          AND NOT EXISTS (SELECT 1 FROM navigation_reservation reservation
            WHERE reservation.job_id=job.id)"""
    )
    over_budget = connection.exec_driver_sql(
        """SELECT s.id FROM session s WHERE s.nav_used + coalesce((
          SELECT sum(r.cost) FROM navigation_reservation r
          WHERE r.session_id=s.id AND r.state='reserved'),0) > s.nav_budget LIMIT 1"""
    ).first()
    if over_budget is not None:
        raise RuntimeError(
            f"cannot apply {VERSION}: active navigation reservations exceed budget"
        )
    for name in (
        "candidate_profile_urn_is_write_once",
        "candidate_profile_quarantine_is_one_way",
        "profile_fetch_projection_is_immutable",
        "profile_section_error_requires_lineage",
        "section_reference_requires_fetch",
        "fetch_section_error_insert_collision",
        "section_reference_insert_collision",
    ):
        connection.exec_driver_sql(f'DROP TRIGGER IF EXISTS "{name}"')

    statements = (
        """CREATE TRIGGER candidate_profile_urn_insert_requires_observation
        BEFORE INSERT ON candidate FOR EACH ROW WHEN NEW.profile_urn IS NOT NULL
        BEGIN SELECT RAISE(ABORT,
          'candidate URN requires immutable profile observation'); END""",
        """CREATE TRIGGER candidate_insert_collision BEFORE INSERT ON candidate
        FOR EACH ROW WHEN EXISTS (SELECT 1 FROM candidate c WHERE c.id=NEW.id)
        BEGIN SELECT RAISE(ABORT,
          'candidate identity already exists; recipient identity; purged evidence');
        END""",
        """CREATE TRIGGER candidate_profile_urn_requires_observation
        BEFORE UPDATE OF profile_urn ON candidate FOR EACH ROW WHEN
          (OLD.profile_urn IS NOT NULL AND NEW.profile_urn IS NOT OLD.profile_urn)
          OR (OLD.profile_urn IS NULL AND NEW.profile_urn IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM profile_identity_observation observation
            WHERE observation.candidate_id=OLD.id
              AND observation.observed_urn=NEW.profile_urn
              AND observation.verdict='accepted'))
        BEGIN SELECT RAISE(ABORT,
          'URN needs observation; recipient identity is immutable'); END""",
        """CREATE TRIGGER candidate_profile_quarantine_requires_observation
        BEFORE UPDATE OF profile_urn_quarantined ON candidate FOR EACH ROW WHEN
          (OLD.profile_urn_quarantined=1 AND NEW.profile_urn_quarantined<>1)
          OR (OLD.profile_urn_quarantined=0 AND NEW.profile_urn_quarantined=1
            AND NOT EXISTS (SELECT 1 FROM profile_identity_observation observation
              WHERE observation.candidate_id=OLD.id
                AND observation.verdict IN ('conflict','url_mismatch')))
        BEGIN SELECT RAISE(ABORT,
          'candidate quarantine requires immutable profile observation'); END""",
        """CREATE TRIGGER candidate_profile_contract_error_is_final
        BEFORE UPDATE OF profile_contract_error ON candidate FOR EACH ROW
        WHEN OLD.profile_contract_error IS NOT NULL
          AND NEW.profile_contract_error IS NOT OLD.profile_contract_error
        BEGIN SELECT RAISE(ABORT, 'candidate profile contract error is final'); END""",
        """CREATE TRIGGER profile_identity_observation_requires_exact_verdict
        BEFORE INSERT ON profile_identity_observation FOR EACH ROW WHEN NOT EXISTS (
          SELECT 1 FROM profile_fetch pf JOIN candidate c ON c.id=pf.candidate_id
          WHERE pf.id=NEW.fetch_id AND c.id=NEW.candidate_id
            AND json_extract(pf.projection_payload,'$.url') IS NEW.returned_url
            AND json_extract(pf.projection_payload,'$.profile_urn') IS NEW.observed_urn
            AND ((NEW.verdict='missing' AND NEW.observed_urn IS NULL
                  AND rtrim(NEW.returned_url,'/')=rtrim(c.profile_url,'/')
                    COLLATE unicode_casefold)
              OR (NEW.verdict='accepted' AND NEW.observed_urn IS NOT NULL
                  AND c.profile_urn IS NULL
                  AND rtrim(NEW.returned_url,'/')=rtrim(c.profile_url,'/')
                    COLLATE unicode_casefold)
              OR (NEW.verdict='same' AND NEW.observed_urn=c.profile_urn
                  AND rtrim(NEW.returned_url,'/')=rtrim(c.profile_url,'/')
                    COLLATE unicode_casefold)
              OR (NEW.verdict='conflict' AND NEW.observed_urn IS NOT NULL
                  AND c.profile_urn IS NOT NULL
                  AND NEW.observed_urn<>c.profile_urn
                  AND rtrim(NEW.returned_url,'/')=rtrim(c.profile_url,'/')
                    COLLATE unicode_casefold)
              OR (NEW.verdict='url_mismatch'
                  AND NEW.returned_url LIKE 'https://www.linkedin.com/in/%'
                  AND rtrim(NEW.returned_url,'/')<>rtrim(c.profile_url,'/')
                    COLLATE unicode_casefold)))
        BEGIN SELECT RAISE(ABORT,
          'profile identity observation verdict is not exact'); END""",
        """CREATE TRIGGER profile_fetch_projection_is_immutable
        BEFORE UPDATE OF projection_payload, projection_source, contract_error
        ON profile_fetch FOR EACH ROW WHEN OLD.raw_response IS NOT NULL
          AND OLD.raw_response<>'null'
        BEGIN SELECT RAISE(ABORT, 'profile projection history is immutable'); END""",
        """CREATE TRIGGER fetch_section_error_insert_collision
        BEFORE INSERT ON section_error FOR EACH ROW WHEN NEW.fetch_id IS NOT NULL
          AND EXISTS (SELECT 1 FROM section_error existing
            WHERE existing.id=NEW.id OR (existing.fetch_id=NEW.fetch_id
              AND existing.section_name=NEW.section_name))
        BEGIN SELECT RAISE(ABORT,
          'profile section error source item already exists'); END""",
        """CREATE TRIGGER profile_section_error_requires_lineage
        BEFORE INSERT ON section_error FOR EACH ROW WHEN NEW.fetch_id IS NOT NULL AND
          (NEW.search_run_id IS NOT NULL OR NEW.candidate_id IS NULL OR NOT EXISTS (
            SELECT 1 FROM profile_fetch pf,
              json_each(json_extract(pf.projection_payload,'$.section_errors')) item
            WHERE pf.id=NEW.fetch_id AND pf.candidate_id=NEW.candidate_id
              AND pf.contract_error IS NULL AND item.key=NEW.section_name
              AND json(item.value)=json(NEW.source_item)
              AND json_extract(item.value,'$.error_type')=NEW.error_type
              AND json_extract(item.value,'$.error_message')=NEW.error_message))
        BEGIN SELECT RAISE(ABORT,
          'profile section error requires exact committed source item'); END""",
        """CREATE TRIGGER section_reference_insert_collision
        BEFORE INSERT ON section_reference FOR EACH ROW WHEN EXISTS (
          SELECT 1 FROM section_reference existing WHERE existing.id=NEW.id OR
            (existing.fetch_id=NEW.fetch_id AND
             existing.section_name=NEW.section_name AND
             existing.source_position=NEW.source_position))
        BEGIN SELECT RAISE(ABORT,
          'profile reference source position already exists'); END""",
        """CREATE TRIGGER section_reference_requires_fetch
        BEFORE INSERT ON section_reference FOR EACH ROW WHEN NEW.fetch_id IS NULL OR
          NEW.source_position IS NULL OR NEW.source_position<0 OR NOT EXISTS (
            SELECT 1 FROM profile_fetch pf,
              json_each(json_extract(pf.projection_payload,'$.references')) group_item
            WHERE pf.id=NEW.fetch_id AND pf.candidate_id=NEW.candidate_id
              AND pf.contract_error IS NULL AND group_item.key=NEW.section_name
              AND json_type(group_item.value)='array'
              AND json_extract(group_item.value,
                '$[' || NEW.source_position || '].kind')=NEW.kind
              AND coalesce(json_extract(group_item.value,
                '$[' || NEW.source_position || '].url'),'')=NEW.url
              AND json_extract(group_item.value,
                '$[' || NEW.source_position || '].text') IS NEW.text
              AND json_extract(group_item.value,
                '$[' || NEW.source_position || '].context') IS NEW.context
              AND json_extract(group_item.value,
                '$[' || NEW.source_position || '].value') IS NEW.value)
        BEGIN SELECT RAISE(ABORT,
          'profile reference requires exact committed source position'); END""",
        """CREATE TRIGGER navigation_reservation_insert_collision
        BEFORE INSERT ON navigation_reservation FOR EACH ROW WHEN EXISTS (
          SELECT 1 FROM navigation_reservation existing
          WHERE existing.job_id=NEW.job_id)
        BEGIN SELECT RAISE(ABORT, 'navigation reservation already exists'); END""",
        """CREATE TRIGGER navigation_reservation_identity_is_immutable
        BEFORE UPDATE OF job_id, session_id, cost, reserved_at
        ON navigation_reservation FOR EACH ROW
        BEGIN SELECT RAISE(ABORT,
          'navigation reservation identity is immutable'); END""",
        """CREATE TRIGGER navigation_reservation_transition_is_valid
        BEFORE UPDATE ON navigation_reservation FOR EACH ROW WHEN NOT (
          (OLD.state='reserved' AND NEW.state IN ('charged','released'))
          OR (OLD.state='charged' AND NEW.state IN ('charged','released')))
        BEGIN SELECT RAISE(ABORT, 'invalid navigation reservation transition'); END""",
        """CREATE TRIGGER job_attempt_external_phase_is_monotonic
        BEFORE UPDATE OF external_call_started_at ON job_attempt FOR EACH ROW WHEN
          OLD.external_call_started_at IS NOT NULL
          OR NEW.external_call_started_at IS NULL
          OR OLD.outcome<>'running' OR OLD.finished_at IS NOT NULL
        BEGIN SELECT RAISE(ABORT, 'job attempt external phase is immutable'); END""",
    )
    for statement in statements:
        connection.exec_driver_sql(statement)
