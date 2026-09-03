"""Close terminal-null projection and identity-attestation gaps."""

from __future__ import annotations

from typing import cast

from sqlalchemy import Connection, Table

from linkedin_dashboard.db.models import ProfileFetch

VERSION = "0022_terminal_projection_authority"


def _rebuild_profile_fetch(connection: Connection) -> None:
    objects = connection.exec_driver_sql(
        "SELECT type,name,sql FROM sqlite_master WHERE tbl_name='profile_fetch' "
        "AND type IN ('trigger','index') AND name NOT LIKE 'sqlite_%'"
    ).all()
    definitions: list[str] = []
    for kind, name, sql in objects:
        if isinstance(sql, str):
            definitions.append(sql)
        quoted = str(name).replace('"', '""')
        connection.exec_driver_sql(f'DROP {str(kind).upper()} "{quoted}"')

    columns = (
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
    old = f"__{VERSION}_profile_fetch"
    connection.exec_driver_sql("PRAGMA legacy_alter_table=ON")
    connection.exec_driver_sql(f'ALTER TABLE profile_fetch RENAME TO "{old}"')
    cast(Table, ProfileFetch.__table__).create(connection)
    joined = ", ".join(columns)
    connection.exec_driver_sql(
        f"INSERT INTO profile_fetch ({joined}) SELECT {joined} FROM {old}"
    )
    connection.exec_driver_sql(f'DROP TABLE "{old}"')
    connection.exec_driver_sql("PRAGMA legacy_alter_table=OFF")
    for definition in definitions:
        connection.exec_driver_sql(definition)


def apply(connection: Connection) -> None:
    _rebuild_profile_fetch(connection)
    for name in (
        "profile_fetch_projection_is_immutable",
        "profile_identity_observation_requires_exact_verdict",
        "candidate_profile_urn_requires_observation",
    ):
        connection.exec_driver_sql(f'DROP TRIGGER IF EXISTS "{name}"')

    statements = (
        """CREATE TRIGGER profile_fetch_projection_is_immutable
        BEFORE UPDATE OF raw_response,projection_payload,
          projection_source,contract_error
        ON profile_fetch FOR EACH ROW WHEN
          (OLD.finished_at IS NOT NULL OR OLD.processed_at IS NOT NULL OR
           (OLD.raw_response IS NOT NULL AND OLD.raw_response<>'null')) AND (
            NEW.raw_response IS NOT OLD.raw_response OR
            NEW.projection_payload IS NOT OLD.projection_payload OR
            NEW.projection_source IS NOT OLD.projection_source OR
            NEW.contract_error IS NOT OLD.contract_error)
        BEGIN SELECT RAISE(ABORT, 'terminal profile projection is immutable'); END""",
        """CREATE TRIGGER profile_identity_observation_requires_exact_verdict
        BEFORE INSERT ON profile_identity_observation FOR EACH ROW WHEN NOT EXISTS (
          SELECT 1 FROM profile_fetch pf JOIN candidate c ON c.id=pf.candidate_id
          WHERE pf.id=NEW.fetch_id AND c.id=NEW.candidate_id
            AND pf.raw_response IS NOT NULL AND pf.raw_response<>'null'
            AND pf.contract_error IS NULL
            AND EXISTS (SELECT 1 FROM job_attempt ja WHERE ja.job_id=pf.job_id
              AND ja.raw_response=pf.raw_response)
            AND ((pf.projection_source='structured_content'
                  AND json_type(pf.raw_response,'$.structuredContent')='object'
                  AND json(pf.projection_payload)=json(json_extract(
                    pf.raw_response,'$.structuredContent')))
              OR (pf.projection_source='wrapped_result'
                  AND json_type(
                    pf.raw_response,'$.structuredContent.result')='object'
                  AND (SELECT count(*) FROM json_each(json_extract(
                    pf.raw_response,'$.structuredContent')))=1
                  AND json(pf.projection_payload)=json(json_extract(
                    pf.raw_response,'$.structuredContent.result'))))
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
          'profile identity observation requires an attested fetch'); END""",
        """CREATE TRIGGER candidate_profile_urn_requires_observation
        BEFORE UPDATE OF profile_urn ON candidate FOR EACH ROW WHEN
          (OLD.profile_urn IS NOT NULL AND NEW.profile_urn IS NOT OLD.profile_urn)
          OR (OLD.profile_urn IS NULL AND NEW.profile_urn IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM profile_identity_observation observation
            JOIN profile_fetch pf ON pf.id=observation.fetch_id
            WHERE observation.candidate_id=OLD.id
              AND observation.observed_urn=NEW.profile_urn
              AND observation.verdict='accepted'
              AND pf.candidate_id=OLD.id AND pf.contract_error IS NULL
              AND pf.raw_response IS NOT NULL AND pf.raw_response<>'null'
              AND EXISTS (SELECT 1 FROM job_attempt ja WHERE ja.job_id=pf.job_id
                AND ja.raw_response=pf.raw_response)
              AND json_extract(pf.projection_payload,'$.url')
                    IS observation.returned_url
              AND json_extract(pf.projection_payload,'$.profile_urn')
                    IS observation.observed_urn
              AND ((pf.projection_source='structured_content'
                    AND json_type(pf.raw_response,'$.structuredContent')='object'
                    AND json(pf.projection_payload)=json(json_extract(
                      pf.raw_response,'$.structuredContent')))
                OR (pf.projection_source='wrapped_result'
                    AND json_type(
                      pf.raw_response,'$.structuredContent.result')='object'
                    AND (SELECT count(*) FROM json_each(json_extract(
                      pf.raw_response,'$.structuredContent')))=1
                    AND json(pf.projection_payload)=json(json_extract(
                      pf.raw_response,'$.structuredContent.result'))))))
        BEGIN SELECT RAISE(ABORT,
          'URN needs attested observation; recipient identity is immutable'); END""",
    )
    for statement in statements:
        connection.exec_driver_sql(statement)
