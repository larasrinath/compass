from __future__ import annotations

from typing import cast

from sqlalchemy import Connection, Table

from linkedin_dashboard.db.models import Candidate

VERSION = "0018_candidate_identity"


def _columns(connection: Connection) -> set[str]:
    return {
        str(row[1])
        for row in connection.exec_driver_sql('PRAGMA table_xinfo("candidate")').all()
    }


def _drop_candidate_objects(connection: Connection) -> None:
    for kind, name in connection.exec_driver_sql(
        "SELECT type, name FROM sqlite_master "
        "WHERE tbl_name='candidate' AND type IN ('trigger','index') "
        "AND name NOT LIKE 'sqlite_%'"
    ).all():
        quoted = str(name).replace('"', '""')
        connection.exec_driver_sql(f'DROP {str(kind).upper()} "{quoted}"')


def apply(connection: Connection) -> None:
    duplicate = connection.exec_driver_sql(
        "SELECT 1 FROM candidate "
        "GROUP BY session_id, username COLLATE unicode_casefold "
        "HAVING count(*) > 1 LIMIT 1"
    ).first()
    if duplicate is not None:
        raise RuntimeError(
            f"cannot apply {VERSION}: duplicate normalized candidate identity"
        )
    if "dedupe_key" not in _columns(connection):
        return

    old = f"__{VERSION}_candidate"
    _drop_candidate_objects(connection)
    connection.exec_driver_sql("PRAGMA legacy_alter_table=ON")
    connection.exec_driver_sql(f'ALTER TABLE candidate RENAME TO "{old}"')
    cast(Table, Candidate.__table__).create(connection)
    connection.exec_driver_sql(
        f"""INSERT INTO candidate
          (id, session_id, username, profile_url, display_name, profile_urn,
           first_seen_at, stage, retrieval_status)
        SELECT id, session_id, username, profile_url, display_name, profile_urn,
               first_seen_at, stage, retrieval_status FROM "{old}"
        """
    )
    connection.exec_driver_sql(f'DROP TABLE "{old}"')
    connection.exec_driver_sql("PRAGMA legacy_alter_table=OFF")
