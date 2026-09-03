from __future__ import annotations

from typing import cast

from sqlalchemy import Connection, Table

from linkedin_dashboard.db.models import BriefTerm, CompanyLookup

VERSION = "0017_role_discovery"

TRIGGER_NAMES = (
    "role_brief_append_only",
    "brief_skill_is_immutable",
    "brief_term_is_immutable",
)

INDEX_NAMES = ("candidate_session_dedupe_key",)


def _columns(connection: Connection, table: str) -> set[str]:
    return {
        str(row[1])
        for row in connection.exec_driver_sql(f"PRAGMA table_info({table})").all()
    }


def _upgrade_search_run(connection: Connection) -> None:
    if "job_id" in _columns(connection, "search_run"):
        return
    if connection.exec_driver_sql("SELECT EXISTS (SELECT 1 FROM search_run)").scalar():
        raise RuntimeError(
            f"cannot apply {VERSION}: search_run rows predate durable discovery"
        )
    # M2 is the first code that can write search rows. The table is therefore
    # empty on an upgrade from M1, which lets us replace its lifecycle CHECK
    # without weakening foreign-key enforcement or deleting user data.
    connection.exec_driver_sql("DROP TABLE search_run")
    connection.exec_driver_sql(
        """
        CREATE TABLE search_run (
          id VARCHAR(36) PRIMARY KEY NOT NULL,
          session_id VARCHAR(36) NOT NULL REFERENCES session(id) ON DELETE CASCADE,
          brief_id VARCHAR(36) NOT NULL REFERENCES role_brief(id) ON DELETE CASCADE,
          job_id VARCHAR(36) NOT NULL REFERENCES job(id) ON DELETE RESTRICT,
          created_at VARCHAR(32) NOT NULL,
          keywords TEXT NOT NULL,
          location TEXT,
          network JSON,
          current_company TEXT,
          result_url TEXT,
          raw_response JSON,
          processed_at VARCHAR(32),
          reference_count INTEGER NOT NULL DEFAULT 0,
          person_reference_count INTEGER NOT NULL DEFAULT 0,
          status VARCHAR(16) NOT NULL,
          CONSTRAINT ck_search_run_status CHECK (
            status IN ('queued','running','ok','partial','rate_limited','failed',
                       'interrupted','cancelled')
          ),
          CONSTRAINT uq_search_run_job UNIQUE (job_id)
        )
        """
    )


def apply(connection: Connection) -> None:
    cast(Table, BriefTerm.__table__).create(connection, checkfirst=True)
    cast(Table, CompanyLookup.__table__).create(connection, checkfirst=True)
    _upgrade_search_run(connection)

    candidate_columns = _columns(connection, "candidate")
    if "dedupe_key" not in candidate_columns:
        connection.exec_driver_sql("ALTER TABLE candidate ADD COLUMN dedupe_key TEXT")
        connection.exec_driver_sql(
            "UPDATE candidate SET dedupe_key = lower(username) WHERE dedupe_key IS NULL"
        )
    skill_columns = _columns(connection, "brief_skill")
    if "position" not in skill_columns:
        connection.exec_driver_sql(
            "ALTER TABLE brief_skill ADD COLUMN position INTEGER NOT NULL DEFAULT 0"
        )
        connection.exec_driver_sql(
            """
            UPDATE brief_skill AS current
               SET position = (
                 SELECT count(*) - 1
                   FROM brief_skill AS earlier
                  WHERE earlier.brief_id = current.brief_id
                    AND earlier.kind = current.kind
                    AND earlier.rowid <= current.rowid
               )
            """
        )
    # M0/M1 exposed these JSON columns in the schema without a brief-writing
    # API. Still preserve any hand-seeded values when adding alias-aware rows.
    for kind, column in (
        ("target_title", "target_titles"),
        ("industry", "industries"),
    ):
        connection.exec_driver_sql(
            f"""
            INSERT OR IGNORE INTO brief_term
              (id, brief_id, kind, term, term_key, aliases, position)
            SELECT lower(hex(randomblob(16))), role_brief.id, ?,
                   item.value, lower(item.value), json('[]'), CAST(item.key AS INTEGER)
              FROM role_brief, json_each(role_brief.{column}) AS item
             WHERE item.type = 'text' AND trim(item.value) <> ''
            """,
            (kind,),
        )
    reference_columns = _columns(connection, "candidate_ref")
    if "extra" not in reference_columns:
        connection.exec_driver_sql(
            "ALTER TABLE candidate_ref ADD COLUMN extra JSON NOT NULL DEFAULT '{}'"
        )

    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS candidate_session_dedupe_key "
        "ON candidate(session_id, "
        "CASE WHEN dedupe_key IS NULL OR dedupe_key = '' "
        "THEN lower(username) ELSE dedupe_key END)"
    )
    connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS role_brief_append_only
        BEFORE UPDATE OF superseded_at, version, created_at, job_description,
          target_titles, location, industries, positive_keywords,
          negative_keywords, message_tone, weights_version ON role_brief
        FOR EACH ROW
        WHEN NEW.version IS NOT OLD.version
          OR NEW.created_at IS NOT OLD.created_at
          OR NEW.job_description IS NOT OLD.job_description
          OR NEW.target_titles IS NOT OLD.target_titles
          OR NEW.location IS NOT OLD.location
          OR NEW.industries IS NOT OLD.industries
          OR NEW.positive_keywords IS NOT OLD.positive_keywords
          OR NEW.negative_keywords IS NOT OLD.negative_keywords
          OR NEW.message_tone IS NOT OLD.message_tone
          OR NEW.weights_version IS NOT OLD.weights_version
          OR NEW.superseded_at IS NULL
          OR OLD.superseded_at IS NOT NULL
        BEGIN
          SELECT RAISE(ABORT, 'role brief versions are append-only');
        END
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS brief_skill_is_immutable
        BEFORE UPDATE ON brief_skill
        FOR EACH ROW
        BEGIN
          SELECT RAISE(ABORT, 'brief skill versions are immutable');
        END
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS brief_term_is_immutable
        BEFORE UPDATE ON brief_term
        FOR EACH ROW
        BEGIN
          SELECT RAISE(ABORT, 'brief term versions are immutable');
        END
        """
    )
