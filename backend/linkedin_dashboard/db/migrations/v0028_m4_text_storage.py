"""Require canonical M4 brief inputs to use SQLite TEXT storage."""

from __future__ import annotations

from sqlalchemy import Connection

from linkedin_dashboard.db.migrations import v0025_m4_semantic_integrity as v25
from linkedin_dashboard.db.migrations import v0027_m4_bounded_manifests as v27

VERSION = "0028_m4_text_storage"

_STORAGE_CHECKS = (
    ("role_brief", "location", "typeof(subject.location)<>'text'"),
    (
        "role_brief",
        "scoring_inputs",
        "(subject.scoring_inputs IS NOT NULL "
        "AND typeof(subject.scoring_inputs)<>'text') "
        "OR (subject.sealed_at IS NOT NULL AND subject.scoring_inputs IS NULL)",
    ),
    ("brief_skill", "aliases", "typeof(subject.aliases)<>'text'"),
    ("brief_term", "aliases", "typeof(subject.aliases)<>'text'"),
    ("brief_credential", "aliases", "typeof(subject.aliases)<>'text'"),
)

TRIGGER_NAMES = (
    "role_brief_text_storage_insert_v28",
    "role_brief_text_storage_update_v28",
    "brief_skill_alias_text_storage_v28",
    "brief_term_alias_text_storage_v28",
    "brief_credential_alias_text_storage_v28",
)

STATEMENTS = (
    """CREATE TRIGGER role_brief_text_storage_insert_v28
       BEFORE INSERT ON role_brief FOR EACH ROW
       WHEN typeof(NEW.location)<>'text'
         OR (NEW.scoring_inputs IS NOT NULL
             AND typeof(NEW.scoring_inputs)<>'text')
         OR (NEW.sealed_at IS NOT NULL AND NEW.scoring_inputs IS NULL)
       BEGIN
         SELECT RAISE(ABORT, 'role brief scoring inputs are not canonical');
       END""",
    """CREATE TRIGGER role_brief_text_storage_update_v28
       BEFORE UPDATE OF location,scoring_inputs ON role_brief FOR EACH ROW
       WHEN typeof(NEW.location)<>'text'
         OR (NEW.scoring_inputs IS NOT NULL
             AND typeof(NEW.scoring_inputs)<>'text')
         OR (NEW.sealed_at IS NOT NULL AND NEW.scoring_inputs IS NULL)
       BEGIN
         SELECT RAISE(ABORT, 'role brief scoring inputs are not canonical');
       END""",
    """CREATE TRIGGER brief_skill_alias_text_storage_v28
       BEFORE INSERT ON brief_skill FOR EACH ROW
       WHEN typeof(NEW.aliases)<>'text'
       BEGIN SELECT RAISE(ABORT, 'brief skill scoring source is invalid'); END""",
    """CREATE TRIGGER brief_term_alias_text_storage_v28
       BEFORE INSERT ON brief_term FOR EACH ROW
       WHEN typeof(NEW.aliases)<>'text'
       BEGIN SELECT RAISE(ABORT, 'brief term scoring source is invalid'); END""",
    """CREATE TRIGGER brief_credential_alias_text_storage_v28
       BEFORE INSERT ON brief_credential FOR EACH ROW
       WHEN typeof(NEW.aliases)<>'text'
       BEGIN
         SELECT RAISE(ABORT, 'brief credential scoring source is invalid');
       END""",
)


def _preflight_storage_classes(connection: Connection) -> None:
    for table, column, invalid_predicate in _STORAGE_CHECKS:
        if table == "role_brief":
            source = '"role_brief" subject'
            session_column = "subject.session_id"
        else:
            source = (
                f'"{table}" subject LEFT JOIN role_brief owner '
                "ON owner.id=subject.brief_id"
            )
            session_column = "owner.session_id"
        invalid = connection.exec_driver_sql(
            f'SELECT subject.id,typeof(subject."{column}"),{session_column} '
            f"FROM {source} "
            f"WHERE {invalid_predicate} ORDER BY subject.id LIMIT 1"
        ).first()
        if invalid is not None:
            if isinstance(invalid[2], str) and invalid[2]:
                purge = (
                    f"purge affected session {invalid[2]!r} through the supported "
                    "session-purge workflow"
                )
            else:
                purge = (
                    "purge the session owning the affected brief through the "
                    "supported session-purge workflow"
                )
            raise RuntimeError(
                f"cannot apply {VERSION}: {table}.{column} for row {invalid[0]} "
                f"uses SQLite {invalid[1]} storage; restore canonical TEXT from a "
                f"known-good value or {purge} before retrying"
            )


def apply(connection: Connection) -> None:
    # Exact v27 databases may contain values which SQLite's JSON functions accept
    # as BLOBs.  Inspect data before dropping a single guard so failure rolls back
    # without normalizing, rewriting, or partially replacing the schema.
    _preflight_storage_classes(connection)
    v25._preflight(connection)
    for name in (
        *(name for name in v25.TRIGGER_NAMES if name != "phase_gate_manifest_insert"),
        *v27.TRIGGER_NAMES,
        *TRIGGER_NAMES,
    ):
        connection.exec_driver_sql(f'DROP TRIGGER IF EXISTS "{name}"')
    connection.exec_driver_sql("DROP INDEX IF EXISTS score_signal_identity_v25")
    for statement in (*v25.STATEMENTS, *v27.STATEMENTS, *STATEMENTS):
        connection.exec_driver_sql(statement)
