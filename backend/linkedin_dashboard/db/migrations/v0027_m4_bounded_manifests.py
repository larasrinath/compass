"""Converge M4 scoring manifests on total, bounded source validation."""

from __future__ import annotations

from sqlalchemy import Connection

from linkedin_dashboard.db.migrations import v0025_m4_semantic_integrity as v25
from linkedin_dashboard.db.scoring_manifest import (
    MAX_ALIAS_LENGTH,
    MAX_ALIASES_PER_TERM,
    MAX_BRIEF_CANONICAL_CHARS,
    MAX_BRIEF_VOCABULARY,
    MAX_TERM_LENGTH,
)

VERSION = "0027_m4_bounded_manifests"


def _category_rows(table: str, brief: str, kind: str | None) -> str:
    predicate = "" if kind is None else f" AND kind={kind}"
    return f"SELECT term,aliases FROM {table} WHERE brief_id={brief}{predicate}"


def _new_category_rows(table: str, kind: str | None) -> str:
    current = _category_rows(table, "NEW.brief_id", kind)
    return f"SELECT NEW.term AS term,NEW.aliases AS aliases UNION ALL {current}"


def _source_collision_invalid(table: str, kind: str | None) -> str:
    rows = _new_category_rows(table, kind)
    return f"""
EXISTS (
  SELECT 1 FROM ({rows}) owner,json_each(owner.aliases) owner_alias,
                ({rows}) other
  WHERE owner.term<>other.term COLLATE scoring_normalized_v1
    AND (owner_alias.value=other.term COLLATE scoring_normalized_v1
      OR EXISTS (SELECT 1 FROM json_each(other.aliases) other_alias
                 WHERE owner_alias.value=other_alias.value
                   COLLATE scoring_normalized_v1)))
"""


_EXISTING_VOCABULARY = """
(SELECT count(*)+coalesce(sum(json_array_length(source.aliases)),0) FROM (
  SELECT aliases FROM brief_skill WHERE brief_id=NEW.brief_id
  UNION ALL SELECT aliases FROM brief_term WHERE brief_id=NEW.brief_id
  UNION ALL SELECT aliases FROM brief_credential WHERE brief_id=NEW.brief_id
) source)
"""

_EXISTING_CHARS = """
(SELECT coalesce(sum(length(source.term)+coalesce((
  SELECT sum(length(alias.value)) FROM json_each(source.aliases) alias),0)),0)
 FROM (
  SELECT term,aliases FROM brief_skill WHERE brief_id=NEW.brief_id
  UNION ALL SELECT term,aliases FROM brief_term WHERE brief_id=NEW.brief_id
  UNION ALL SELECT term,aliases FROM brief_credential WHERE brief_id=NEW.brief_id
 ) source)
"""


def _child_invalid(table: str, kind: str | None) -> str:
    return f"""
CASE
WHEN typeof(NEW.term) IS NOT 'text'
  OR length(NEW.term) NOT BETWEEN 1 AND {MAX_TERM_LENGTH}
  OR NEW.term='' COLLATE scoring_normalized_v1 THEN 1
WHEN json_valid(NEW.aliases) IS NOT 1 THEN 1
WHEN json_type(NEW.aliases) IS NOT 'array' THEN 1
ELSE COALESCE((
  json_array_length(NEW.aliases)>{MAX_ALIASES_PER_TERM}
  OR EXISTS (SELECT 1 FROM json_each(NEW.aliases) alias
             WHERE alias.type IS NOT 'text'
               OR length(alias.value) NOT BETWEEN 1 AND {MAX_ALIAS_LENGTH}
               OR alias.value='' COLLATE scoring_normalized_v1)
  OR {_source_collision_invalid(table, kind)}
  OR 1+json_array_length(NEW.aliases)+{_EXISTING_VOCABULARY}
       >{MAX_BRIEF_VOCABULARY}
  OR length(NEW.term)+coalesce((SELECT sum(length(alias.value))
       FROM json_each(NEW.aliases) alias),0)+{_EXISTING_CHARS}
       >{MAX_BRIEF_CANONICAL_CHARS}
),1) END
"""


def _collision_exists(table: str) -> str:
    alternatives = {
        "brief_skill": "old.id=NEW.id",
        "brief_term": (
            "old.id=NEW.id OR (old.brief_id=NEW.brief_id "
            "AND old.kind=NEW.kind AND old.term_key=NEW.term_key)"
        ),
        "brief_credential": (
            "old.id=NEW.id OR (old.brief_id=NEW.brief_id AND old.term_key=NEW.term_key)"
        ),
    }
    return f"EXISTS (SELECT 1 FROM {table} old WHERE {alternatives[table]})"


TRIGGER_NAMES = (
    "brief_skill_insert_collision_v27",
    "brief_term_insert_collision_v27",
    "brief_skill_source_shape_v27",
    "brief_term_source_shape_v27",
    "brief_credential_source_shape_v27",
)

STATEMENTS = (
    """CREATE TRIGGER brief_skill_insert_collision_v27
       BEFORE INSERT ON brief_skill FOR EACH ROW WHEN EXISTS (
         SELECT 1 FROM brief_skill old WHERE old.id=NEW.id)
       BEGIN SELECT RAISE(ABORT, 'brief skill already exists'); END""",
    """CREATE TRIGGER brief_term_insert_collision_v27
       BEFORE INSERT ON brief_term FOR EACH ROW WHEN EXISTS (
         SELECT 1 FROM brief_term old WHERE old.id=NEW.id
           OR (old.brief_id=NEW.brief_id AND old.kind=NEW.kind
               AND old.term_key=NEW.term_key))
       BEGIN SELECT RAISE(ABORT, 'brief term already exists'); END""",
    f"""CREATE TRIGGER brief_skill_source_shape_v27
       BEFORE INSERT ON brief_skill FOR EACH ROW
       WHEN NOT ({_collision_exists("brief_skill")})
         AND ({_child_invalid("brief_skill", "NEW.kind")})
       BEGIN SELECT RAISE(ABORT, 'brief skill scoring source is invalid'); END""",
    f"""CREATE TRIGGER brief_term_source_shape_v27
       BEFORE INSERT ON brief_term FOR EACH ROW
       WHEN NOT ({_collision_exists("brief_term")})
         AND ({_child_invalid("brief_term", "NEW.kind")})
       BEGIN SELECT RAISE(ABORT, 'brief term scoring source is invalid'); END""",
    f"""CREATE TRIGGER brief_credential_source_shape_v27
       BEFORE INSERT ON brief_credential FOR EACH ROW
       WHEN NOT ({_collision_exists("brief_credential")})
         AND ({_child_invalid("brief_credential", None)})
       BEGIN SELECT RAISE(ABORT, 'brief credential scoring source is invalid'); END""",
)


def apply(connection: Connection) -> None:
    # Preflight before replacing any schema object. Unknown historical state is
    # never normalized or discarded; the surrounding migration transaction rolls
    # back cleanly on the first invalid brief.
    v25._prepare_brief_manifests(connection)
    v25._preflight(connection)
    for name in (
        *(name for name in v25.TRIGGER_NAMES if name != "phase_gate_manifest_insert"),
        *TRIGGER_NAMES,
    ):
        connection.exec_driver_sql(f'DROP TRIGGER IF EXISTS "{name}"')
    connection.exec_driver_sql("DROP INDEX IF EXISTS score_signal_identity_v25")
    for statement in (*v25.STATEMENTS, *STATEMENTS):
        connection.exec_driver_sql(statement)
