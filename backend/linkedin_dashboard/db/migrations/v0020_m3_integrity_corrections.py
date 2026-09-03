"""Close profile identity, projection, and append-only integrity gaps."""

from __future__ import annotations

from typing import cast

from sqlalchemy import Connection, Table

from linkedin_dashboard.db.models import ProfileIdentityObservation

VERSION = "0020_m3_integrity_corrections"


def _columns(connection: Connection, table: str) -> set[str]:
    return {
        str(row[1])
        for row in connection.exec_driver_sql(f'PRAGMA table_xinfo("{table}")').all()
    }


def _add_columns(connection: Connection) -> None:
    candidate = _columns(connection, "candidate")
    if "profile_urn_quarantined" not in candidate:
        connection.exec_driver_sql(
            "ALTER TABLE candidate ADD COLUMN profile_urn_quarantined "
            "BOOLEAN NOT NULL DEFAULT 0"
        )
    if "profile_contract_error" not in candidate:
        connection.exec_driver_sql(
            "ALTER TABLE candidate ADD COLUMN profile_contract_error TEXT"
        )
    fetch = _columns(connection, "profile_fetch")
    for name, declaration in (
        ("projection_payload", "JSON"),
        ("projection_source", "VARCHAR(32)"),
        ("contract_error", "TEXT"),
    ):
        if name not in fetch:
            connection.exec_driver_sql(
                f"ALTER TABLE profile_fetch ADD COLUMN {name} {declaration}"
            )


def apply(connection: Connection) -> None:
    _add_columns(connection)
    cast(Table, ProfileIdentityObservation.__table__).create(
        connection, checkfirst=True
    )
    for name in (
        "profile_fetch_requires_job_candidate_lineage",
        "profile_fetch_raw_requires_job_attempt",
        "profile_section_requires_fetch_lineage",
        "profile_section_error_requires_lineage",
        "section_reference_requires_fetch",
        "profile_job_identity_is_immutable",
        "profile_job_no_delete",
        "job_attempt_identity_is_immutable",
    ):
        connection.exec_driver_sql(f'DROP TRIGGER IF EXISTS "{name}"')

    statements = (
        """CREATE UNIQUE INDEX IF NOT EXISTS one_active_profile_fetch_per_candidate
        ON profile_fetch(candidate_id) WHERE outcome IS NULL""",
        """CREATE TRIGGER job_attempt_insert_collision BEFORE INSERT ON job_attempt
        FOR EACH ROW WHEN EXISTS (SELECT 1 FROM job_attempt existing
          WHERE existing.id=NEW.id OR (existing.job_id=NEW.job_id
            AND existing.attempt_number=NEW.attempt_number))
        BEGIN SELECT RAISE(ABORT, 'job attempt history already exists'); END""",
        """CREATE TRIGGER job_attempt_no_delete BEFORE DELETE ON job_attempt
        FOR EACH ROW WHEN EXISTS (SELECT 1 FROM job j JOIN session s
          ON s.id=j.session_id WHERE j.id=OLD.job_id)
        BEGIN SELECT RAISE(ABORT,
          'job attempt history may be deleted only by session purge'); END""",
        """CREATE TRIGGER job_attempt_identity_is_immutable
        BEFORE UPDATE OF id, job_id, attempt_number, worker_token, started_at
        ON job_attempt FOR EACH ROW
        BEGIN SELECT RAISE(ABORT, 'job attempt identity is immutable'); END""",
        """CREATE TRIGGER profile_job_identity_is_immutable
        BEFORE UPDATE OF id, session_id, kind, payload ON job FOR EACH ROW
        WHEN EXISTS (SELECT 1 FROM profile_fetch pf WHERE pf.job_id=OLD.id)
        BEGIN SELECT RAISE(ABORT,
          'profile job identity and payload are immutable'); END""",
        """CREATE TRIGGER profile_job_no_delete BEFORE DELETE ON job FOR EACH ROW
        WHEN EXISTS (SELECT 1 FROM session s WHERE s.id=OLD.session_id)
          AND EXISTS (SELECT 1 FROM profile_fetch pf WHERE pf.job_id=OLD.id)
        BEGIN SELECT RAISE(ABORT,
          'profile jobs may be deleted only by session purge'); END""",
        """CREATE TRIGGER profile_fetch_insert_collision BEFORE INSERT ON profile_fetch
        FOR EACH ROW WHEN EXISTS (SELECT 1 FROM profile_fetch existing
          WHERE existing.id=NEW.id OR existing.job_id=NEW.job_id)
        BEGIN SELECT RAISE(ABORT, 'profile fetch history already exists'); END""",
        """CREATE TRIGGER profile_fetch_no_delete BEFORE DELETE ON profile_fetch
        FOR EACH ROW WHEN EXISTS (SELECT 1 FROM candidate c JOIN session s
          ON s.id=c.session_id WHERE c.id=OLD.candidate_id)
        BEGIN SELECT RAISE(ABORT,
          'profile fetch history may be deleted only by session purge'); END""",
        """CREATE TRIGGER profile_section_insert_collision
        BEFORE INSERT ON profile_section FOR EACH ROW WHEN EXISTS (
          SELECT 1 FROM profile_section existing WHERE existing.id=NEW.id OR
            (existing.candidate_id=NEW.candidate_id AND
             existing.section_name=NEW.section_name AND
             existing.fetch_id=NEW.fetch_id))
        BEGIN SELECT RAISE(ABORT, 'profile section history already exists'); END""",
        """CREATE TRIGGER profile_section_no_delete BEFORE DELETE ON profile_section
        FOR EACH ROW WHEN EXISTS (SELECT 1 FROM candidate c JOIN session s
          ON s.id=c.session_id WHERE c.id=OLD.candidate_id)
        BEGIN SELECT RAISE(ABORT,
          'profile section history may be deleted only by session purge'); END""",
        """CREATE TRIGGER fetch_section_error_insert_collision
        BEFORE INSERT ON section_error FOR EACH ROW WHEN NEW.fetch_id IS NOT NULL
          AND EXISTS (SELECT 1 FROM section_error existing WHERE existing.id=NEW.id)
        BEGIN SELECT RAISE(ABORT,
          'profile section error history already exists'); END""",
        """CREATE TRIGGER fetch_section_error_no_delete BEFORE DELETE ON section_error
        FOR EACH ROW WHEN OLD.fetch_id IS NOT NULL AND EXISTS (
          SELECT 1 FROM profile_fetch pf JOIN candidate c ON c.id=pf.candidate_id
          JOIN session s ON s.id=c.session_id WHERE pf.id=OLD.fetch_id)
        BEGIN SELECT RAISE(ABORT,
          'profile section error history may be deleted only by session purge'); END""",
        """CREATE TRIGGER section_reference_insert_collision
        BEFORE INSERT ON section_reference FOR EACH ROW WHEN EXISTS (
          SELECT 1 FROM section_reference existing WHERE existing.id=NEW.id)
        BEGIN SELECT RAISE(ABORT, 'profile reference history already exists'); END""",
        """CREATE TRIGGER section_reference_no_delete
        BEFORE DELETE ON section_reference FOR EACH ROW WHEN EXISTS (
          SELECT 1 FROM candidate c JOIN session s ON s.id=c.session_id
          WHERE c.id=OLD.candidate_id)
        BEGIN SELECT RAISE(ABORT,
          'profile reference history may be deleted only by session purge'); END""",
        """CREATE TRIGGER parsed_field_insert_collision BEFORE INSERT ON parsed_field
        FOR EACH ROW WHEN EXISTS (SELECT 1 FROM parsed_field existing
          WHERE existing.id=NEW.id)
        BEGIN SELECT RAISE(ABORT, 'parsed field history already exists'); END""",
        """CREATE TRIGGER parsed_field_no_delete BEFORE DELETE ON parsed_field
        FOR EACH ROW WHEN EXISTS (SELECT 1 FROM candidate c JOIN session s
          ON s.id=c.session_id WHERE c.id=OLD.candidate_id)
          AND NOT EXISTS (
            SELECT 1 FROM evidence e
            JOIN score_signal ss ON ss.id=e.score_signal_id
            JOIN score sc ON sc.id=ss.score_id
            WHERE sc.candidate_id=OLD.candidate_id
              AND e.section_name=OLD.section_name
              AND e.span_start=OLD.span_start AND e.span_end=OLD.span_end
              AND e.parsed_field_id IS NULL AND e.purged_at IS NOT NULL)
        BEGIN SELECT RAISE(ABORT,
          'parsed field history may be deleted only by session purge'); END""",
        """CREATE TRIGGER profile_identity_observation_insert_collision
        BEFORE INSERT ON profile_identity_observation FOR EACH ROW WHEN EXISTS (
          SELECT 1 FROM profile_identity_observation existing
          WHERE existing.id=NEW.id OR existing.fetch_id=NEW.fetch_id)
        BEGIN SELECT RAISE(ABORT,
          'profile identity observation already exists'); END""",
        """CREATE TRIGGER profile_identity_observation_is_immutable
        BEFORE UPDATE ON profile_identity_observation FOR EACH ROW
        BEGIN SELECT RAISE(ABORT, 'profile identity observation is immutable'); END""",
        """CREATE TRIGGER profile_identity_observation_no_delete
        BEFORE DELETE ON profile_identity_observation FOR EACH ROW WHEN EXISTS (
          SELECT 1 FROM candidate c JOIN session s ON s.id=c.session_id
          WHERE c.id=OLD.candidate_id)
        BEGIN SELECT RAISE(ABORT,
          'profile identity observation may be deleted only by session purge'); END""",
        """CREATE TRIGGER candidate_profile_urn_is_write_once
        BEFORE UPDATE OF profile_urn ON candidate FOR EACH ROW
        WHEN OLD.profile_urn IS NOT NULL AND NEW.profile_urn IS NOT OLD.profile_urn
        BEGIN SELECT RAISE(ABORT,
          'candidate URN write-once; recipient identity is immutable'); END""",
        """CREATE TRIGGER candidate_profile_quarantine_is_one_way
        BEFORE UPDATE OF profile_urn_quarantined, profile_contract_error ON candidate
        FOR EACH ROW WHEN (OLD.profile_urn_quarantined=1 AND
          NEW.profile_urn_quarantined<>1) OR (OLD.profile_contract_error IS NOT NULL
          AND NEW.profile_contract_error IS NOT OLD.profile_contract_error)
        BEGIN SELECT RAISE(ABORT, 'candidate profile quarantine is immutable'); END""",
        """CREATE TRIGGER profile_fetch_requires_job_candidate_lineage
        BEFORE INSERT ON profile_fetch FOR EACH ROW WHEN NOT EXISTS (
          SELECT 1 FROM job j JOIN candidate c ON c.id=NEW.candidate_id
          WHERE j.id=NEW.job_id AND j.session_id=c.session_id
            AND j.kind='get_person_profile'
            AND NEW.tool='get_person_profile'
            AND c.username=json_extract(j.payload,'$.linkedin_username')
                COLLATE unicode_casefold
            AND json_type(j.payload,'$.sections')='array'
            AND json_type(NEW.requested_sections)='array'
            AND json_array_length(NEW.requested_sections)=
                json_array_length(j.payload,'$.sections')+1
            AND json_extract(NEW.requested_sections,'$[0]')='main_profile'
            AND NOT EXISTS (SELECT 1 FROM json_each(j.payload,'$.sections') item
              WHERE json_extract(NEW.requested_sections,
                '$[' || (CAST(item.key AS INTEGER)+1) || ']') IS NOT item.value)
            AND json_extract(NEW.args,'$.linkedin_username')=
                json_extract(j.payload,'$.linkedin_username')
            AND json(json_extract(NEW.args,'$.sections'))=
                json(json_extract(j.payload,'$.sections'))
            AND json_extract(NEW.args,'$.max_scrolls') IS
                json_extract(j.payload,'$.max_scrolls')
            AND ((json_type(j.payload,'$.parent_job_id') IS NULL
                  AND NEW.parent_fetch_id IS NULL
                  AND NEW.root_fetch_id=NEW.id
                  AND ((NEW.request_stage='stage1'
                        AND json_array_length(j.payload,'$.sections')=1
                        AND json_extract(j.payload,'$.sections[0]')='experience')
                    OR (NEW.request_stage='stage2'
                      AND c.stage IN ('stage1','stage2') AND NOT (
                        json_array_length(j.payload,'$.sections')=1 AND
                        json_extract(j.payload,'$.sections[0]')='experience'))))
              OR (NEW.request_stage='resume' AND NEW.parent_fetch_id IS NOT NULL
                  AND EXISTS (SELECT 1 FROM profile_fetch parent
                    WHERE parent.id=NEW.parent_fetch_id
                      AND parent.candidate_id=NEW.candidate_id
                      AND parent.root_fetch_id=NEW.root_fetch_id
                      AND parent.job_id=
                        json_extract(j.payload,'$.parent_job_id'))))
        ) OR NEW.outcome IS NOT NULL OR NEW.finished_at IS NOT NULL
          OR NEW.duration_ms IS NOT NULL OR NEW.returned_url IS NOT NULL
          OR NEW.processed_at IS NOT NULL
          OR (NEW.raw_response IS NOT NULL AND NEW.raw_response<>'null')
          OR (NEW.projection_payload IS NOT NULL AND NEW.projection_payload<>'null')
          OR NEW.projection_source IS NOT NULL OR NEW.contract_error IS NOT NULL
        BEGIN SELECT RAISE(ABORT,
          'profile fetch requires exact job, candidate, and request lineage'); END""",
        """CREATE TRIGGER profile_fetch_projection_requires_committed_attempt
        BEFORE UPDATE OF raw_response, projection_payload, projection_source
        ON profile_fetch FOR EACH ROW WHEN
          NEW.raw_response IS NOT NULL AND NEW.raw_response<>'null' AND (
            NOT EXISTS (SELECT 1 FROM job_attempt ja WHERE ja.job_id=NEW.job_id
              AND ja.raw_response=NEW.raw_response)
            OR (NEW.contract_error IS NULL AND (
              NEW.projection_payload IS NULL OR NEW.projection_payload='null'
              OR NOT (
              (NEW.projection_source='structured_content' AND
               json_type(NEW.raw_response,'$.structuredContent')='object' AND
               json(NEW.projection_payload)=
                 json(json_extract(NEW.raw_response,'$.structuredContent')))
              OR (NEW.projection_source='wrapped_result' AND
               json_type(NEW.raw_response,'$.structuredContent.result')='object' AND
               (SELECT count(*) FROM json_each(
                 json_extract(NEW.raw_response,'$.structuredContent')))=1 AND
               json(NEW.projection_payload)=
                 json(json_extract(NEW.raw_response,'$.structuredContent.result')))
              )
            ))
          )
        BEGIN SELECT RAISE(ABORT,
          'profile projection must match a committed job attempt'); END""",
        """CREATE TRIGGER profile_fetch_projection_is_immutable
        BEFORE UPDATE OF projection_payload, projection_source, contract_error
        ON profile_fetch FOR EACH ROW WHEN
          (OLD.projection_payload IS NOT NULL AND OLD.projection_payload<>'null' AND
           NEW.projection_payload IS NOT OLD.projection_payload)
          OR (OLD.projection_source IS NOT NULL AND
              NEW.projection_source IS NOT OLD.projection_source)
          OR (OLD.contract_error IS NOT NULL AND
              NEW.contract_error IS NOT OLD.contract_error)
        BEGIN SELECT RAISE(ABORT, 'profile projection history is immutable'); END""",
        """CREATE TRIGGER profile_section_requires_fetch_lineage
        BEFORE INSERT ON profile_section FOR EACH ROW WHEN
          NEW.char_len<>length(NEW.raw_text) OR NOT EXISTS (
            SELECT 1 FROM profile_fetch pf,
              json_each(json_extract(pf.projection_payload,'$.sections')) item
            WHERE pf.id=NEW.fetch_id AND pf.candidate_id=NEW.candidate_id
              AND pf.raw_response IS NOT NULL AND pf.raw_response<>'null'
              AND pf.contract_error IS NULL AND item.key=NEW.section_name
              AND item.type='text' AND item.value=NEW.raw_text)
        BEGIN SELECT RAISE(ABORT,
          'profile section requires exact committed fetch content'); END""",
        """CREATE TRIGGER profile_section_error_requires_lineage
        BEFORE INSERT ON section_error FOR EACH ROW WHEN NEW.fetch_id IS NOT NULL AND
          (NEW.search_run_id IS NOT NULL OR NEW.candidate_id IS NULL OR NOT EXISTS (
            SELECT 1 FROM profile_fetch pf,
              json_each(json_extract(pf.projection_payload,'$.section_errors')) item
            WHERE pf.id=NEW.fetch_id AND pf.candidate_id=NEW.candidate_id
              AND pf.contract_error IS NULL AND item.key=NEW.section_name
              AND json_extract(item.value,'$.error_type')=NEW.error_type
              AND json_extract(item.value,'$.error_message')=NEW.error_message))
        BEGIN SELECT RAISE(ABORT,
          'profile section error requires exact committed fetch content'); END""",
        """CREATE TRIGGER section_reference_requires_fetch
        BEFORE INSERT ON section_reference FOR EACH ROW WHEN NEW.fetch_id IS NULL OR
          NOT EXISTS (SELECT 1 FROM profile_fetch pf,
            json_each(json_extract(pf.projection_payload,'$.references')) group_item,
            json_each(group_item.value) item
            WHERE pf.id=NEW.fetch_id AND pf.candidate_id=NEW.candidate_id
              AND pf.contract_error IS NULL AND group_item.key=NEW.section_name
              AND json_extract(item.value,'$.kind')=NEW.kind
              AND coalesce(json_extract(item.value,'$.url'),'')=NEW.url
              AND json_extract(item.value,'$.text') IS NEW.text
              AND json_extract(item.value,'$.context') IS NEW.context
              AND json_extract(item.value,'$.value') IS NEW.value)
        BEGIN SELECT RAISE(ABORT,
          'profile reference requires exact committed fetch content'); END""",
        """CREATE TRIGGER profile_identity_observation_requires_fetch
        BEFORE INSERT ON profile_identity_observation FOR EACH ROW WHEN NOT EXISTS (
          SELECT 1 FROM profile_fetch pf WHERE pf.id=NEW.fetch_id
            AND pf.candidate_id=NEW.candidate_id
            AND json_extract(pf.projection_payload,'$.url') IS NEW.returned_url
            AND json_extract(pf.projection_payload,'$.profile_urn') IS NEW.observed_urn)
        BEGIN SELECT RAISE(ABORT,
          'profile identity observation requires exact fetch content'); END""",
    )
    for statement in statements:
        connection.exec_driver_sql(statement)
