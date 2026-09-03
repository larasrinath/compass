"""Add exact section lineage and immutable profile-retrieval history."""

from __future__ import annotations

from typing import cast

from sqlalchemy import Connection, Table

from linkedin_dashboard.db.models import ParsedField, ProfileFetch, SectionReference

VERSION = "0019_profile_enrichment"


def _columns(connection: Connection, table: str) -> set[str]:
    return {
        str(row[1])
        for row in connection.exec_driver_sql(f'PRAGMA table_xinfo("{table}")').all()
    }


def apply(connection: Connection) -> None:
    _upgrade_profile_fetch(connection)
    _add_lineage_column(
        connection,
        table="parsed_field",
        column="profile_section_id",
        model=cast(Table, ParsedField.__table__),
    )
    _add_lineage_column(
        connection,
        table="section_reference",
        column="fetch_id",
        model=cast(Table, SectionReference.__table__),
    )

    statements = (
        """CREATE TRIGGER IF NOT EXISTS profile_fetch_requires_job_candidate_lineage
        BEFORE INSERT ON profile_fetch FOR EACH ROW WHEN NOT EXISTS (
          SELECT 1 FROM job j JOIN candidate c ON c.id = NEW.candidate_id
          WHERE j.id = NEW.job_id AND j.session_id = c.session_id
            AND j.kind = 'get_person_profile'
        ) OR NEW.root_fetch_id IS NULL OR (
          NEW.parent_fetch_id IS NULL AND NEW.root_fetch_id <> NEW.id
        ) OR (
          NEW.parent_fetch_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM profile_fetch parent
            WHERE parent.id = NEW.parent_fetch_id
              AND parent.candidate_id = NEW.candidate_id
              AND parent.root_fetch_id = NEW.root_fetch_id
          )
        ) OR (
          NEW.raw_response IS NOT NULL AND NEW.raw_response <> 'null' AND NOT EXISTS (
            SELECT 1 FROM job_attempt ja
            WHERE ja.job_id = NEW.job_id AND ja.raw_response = NEW.raw_response
          )
        )
        BEGIN SELECT RAISE(ABORT,
          'profile fetch requires committed job and candidate lineage'); END""",
        """CREATE TRIGGER IF NOT EXISTS profile_fetch_raw_requires_job_attempt
        BEFORE UPDATE OF raw_response ON profile_fetch FOR EACH ROW WHEN
          NEW.raw_response IS NOT NULL AND NEW.raw_response <> 'null' AND NOT EXISTS (
            SELECT 1 FROM job_attempt ja
            WHERE ja.job_id = NEW.job_id AND ja.raw_response = NEW.raw_response
          )
        BEGIN SELECT RAISE(ABORT,
          'profile fetch raw must match a committed job attempt'); END""",
        """CREATE TRIGGER IF NOT EXISTS profile_fetch_identity_is_immutable
        BEFORE UPDATE ON profile_fetch FOR EACH ROW WHEN
          NEW.id IS NOT OLD.id OR NEW.candidate_id IS NOT OLD.candidate_id OR
          NEW.job_id IS NOT OLD.job_id OR NEW.tool IS NOT OLD.tool OR
          NEW.requested_sections IS NOT OLD.requested_sections OR
          NEW.args IS NOT OLD.args OR NEW.started_at IS NOT OLD.started_at OR
          NEW.request_stage IS NOT OLD.request_stage OR
          NEW.parent_fetch_id IS NOT OLD.parent_fetch_id OR
          NEW.root_fetch_id IS NOT OLD.root_fetch_id OR
          (OLD.raw_response IS NOT NULL AND OLD.raw_response <> 'null' AND
             NEW.raw_response IS NOT OLD.raw_response) OR
          (OLD.returned_url IS NOT NULL AND NEW.returned_url IS NOT OLD.returned_url) OR
          (OLD.finished_at IS NOT NULL AND (
             NEW.finished_at IS NOT OLD.finished_at OR
             NEW.duration_ms IS NOT OLD.duration_ms OR
             NEW.outcome IS NOT OLD.outcome)) OR
          (OLD.processed_at IS NOT NULL AND NEW.processed_at IS NOT OLD.processed_at)
        BEGIN SELECT RAISE(ABORT, 'profile fetch history is immutable'); END""",
        """CREATE TRIGGER IF NOT EXISTS profile_section_is_immutable
        BEFORE UPDATE ON profile_section FOR EACH ROW
        BEGIN SELECT RAISE(ABORT, 'profile section history is immutable'); END""",
        """CREATE TRIGGER IF NOT EXISTS profile_section_requires_fetch_lineage
        BEFORE INSERT ON profile_section FOR EACH ROW WHEN
          NEW.char_len <> length(NEW.raw_text) OR NOT EXISTS (
          SELECT 1 FROM profile_fetch pf
          WHERE pf.id = NEW.fetch_id AND pf.candidate_id = NEW.candidate_id
            AND pf.raw_response IS NOT NULL AND pf.raw_response <> 'null'
        )
        BEGIN SELECT RAISE(ABORT,
          'profile section requires committed fetch lineage'); END""",
        """CREATE TRIGGER IF NOT EXISTS section_error_is_immutable
        BEFORE UPDATE ON section_error FOR EACH ROW WHEN OLD.fetch_id IS NOT NULL
        BEGIN SELECT RAISE(ABORT, 'profile section error history is immutable'); END""",
        """CREATE TRIGGER IF NOT EXISTS profile_section_error_requires_lineage
        BEFORE INSERT ON section_error FOR EACH ROW WHEN NEW.fetch_id IS NOT NULL AND
          (NEW.search_run_id IS NOT NULL OR NEW.candidate_id IS NULL OR NOT EXISTS (
            SELECT 1 FROM profile_fetch pf
            WHERE pf.id = NEW.fetch_id AND pf.candidate_id = NEW.candidate_id
          ))
        BEGIN SELECT RAISE(ABORT,
          'profile section error requires fetch lineage'); END""",
        """CREATE TRIGGER IF NOT EXISTS section_reference_is_immutable
        BEFORE UPDATE ON section_reference FOR EACH ROW
        BEGIN SELECT RAISE(ABORT, 'profile reference history is immutable'); END""",
        """CREATE TRIGGER IF NOT EXISTS section_reference_requires_fetch
        BEFORE INSERT ON section_reference FOR EACH ROW WHEN NEW.fetch_id IS NULL OR
          NOT EXISTS (
            SELECT 1 FROM profile_fetch pf
            WHERE pf.id = NEW.fetch_id AND pf.candidate_id = NEW.candidate_id
          )
        BEGIN
          SELECT RAISE(ABORT, 'profile reference requires fetch provenance');
        END""",
        """CREATE TRIGGER IF NOT EXISTS parsed_field_requires_exact_section_span
        BEFORE INSERT ON parsed_field FOR EACH ROW WHEN
          NEW.profile_section_id IS NULL OR NEW.span_start < 0 OR
          NEW.span_end <= NEW.span_start OR NOT EXISTS (
            SELECT 1 FROM profile_section ps
            WHERE ps.id = NEW.profile_section_id
              AND ps.candidate_id = NEW.candidate_id
              AND ps.section_name = NEW.section_name
              AND NEW.span_end <= length(ps.raw_text)
              AND substr(ps.raw_text, NEW.span_start + 1,
                         NEW.span_end - NEW.span_start) = NEW.snippet
          )
        BEGIN
          SELECT RAISE(ABORT,
            'parsed field requires an exact profile section span');
        END""",
        """CREATE TRIGGER IF NOT EXISTS parsed_field_is_immutable
        BEFORE UPDATE ON parsed_field FOR EACH ROW
        BEGIN SELECT RAISE(ABORT, 'parsed field history is immutable'); END""",
    )
    for statement in statements:
        connection.exec_driver_sql(statement)


def _add_lineage_column(
    connection: Connection, *, table: str, column: str, model: Table
) -> None:
    if column in _columns(connection, table):
        return
    existing_columns = tuple(sorted(_columns(connection, table)))
    old = f"__{VERSION}_{table}"
    connection.exec_driver_sql("PRAGMA legacy_alter_table=ON")
    connection.exec_driver_sql(f'ALTER TABLE "{table}" RENAME TO "{old}"')
    model.create(connection)
    joined = ", ".join(f'"{name}"' for name in existing_columns)
    connection.exec_driver_sql(
        f'INSERT INTO "{table}" ({joined}, "{column}") '
        f'SELECT {joined}, NULL FROM "{old}"'
    )
    connection.exec_driver_sql(f'DROP TABLE "{old}"')
    connection.exec_driver_sql("PRAGMA legacy_alter_table=OFF")


def _upgrade_profile_fetch(connection: Connection) -> None:
    required = {"processed_at", "request_stage", "parent_fetch_id", "root_fetch_id"}
    if required.issubset(_columns(connection, "profile_fetch")):
        return
    old = f"__{VERSION}_profile_fetch"
    connection.exec_driver_sql("PRAGMA legacy_alter_table=ON")
    connection.exec_driver_sql(f'ALTER TABLE profile_fetch RENAME TO "{old}"')
    cast(Table, ProfileFetch.__table__).create(connection)
    connection.exec_driver_sql(
        f"""INSERT INTO profile_fetch
          (id,candidate_id,job_id,tool,requested_sections,args,started_at,
           finished_at,duration_ms,outcome,raw_response,projection_payload,
           projection_source,contract_error,returned_url,processed_at,
           request_stage,parent_fetch_id,root_fetch_id)
        SELECT id,candidate_id,job_id,tool,requested_sections,args,started_at,
          finished_at,duration_ms,outcome,raw_response,NULL,NULL,
          CASE WHEN raw_response IS NOT NULL AND raw_response<>'null'
            THEN 'legacy_m2_unprojected' ELSE NULL END,
          returned_url,coalesce(finished_at,started_at),
          CASE WHEN json_array_length(requested_sections)=2
             AND json_extract(requested_sections,'$[0]')='main_profile'
             AND json_extract(requested_sections,'$[1]')='experience'
            THEN 'stage1' ELSE 'stage2' END,
          NULL,id FROM {old}"""
    )
    connection.exec_driver_sql(f'DROP TABLE "{old}"')
    connection.exec_driver_sql("PRAGMA legacy_alter_table=OFF")
