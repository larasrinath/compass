from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, cast

import pytest
from linkedin_dashboard.api.discovery import BriefInput
from linkedin_dashboard.db import session as db_session
from linkedin_dashboard.db.migrations import v0028_m4_text_storage
from linkedin_dashboard.db.scoring_manifest import build_manifest
from linkedin_dashboard.db.session import Database
from linkedin_dashboard.db.unicode_identity import register_sqlite_unicode_casefold
from linkedin_dashboard.services.brief import BriefValue, TermValue, normalize_brief
from pydantic import ValidationError


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    register_sqlite_unicode_casefold(connection)
    return connection


def _initialize_exact_v27(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    modules = db_session._MIGRATION_MODULES
    db_session._expected_schema.cache_clear()
    database = Database(path)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(db_session, "_MIGRATION_MODULES", modules[:-1])
            database.initialize()
    finally:
        database.dispose()
        db_session._expected_schema.cache_clear()


def _manifest(
    *,
    skills: tuple[tuple[str, tuple[str, ...]], ...] = (),
    titles: tuple[tuple[str, tuple[str, ...]], ...] = (),
    credentials: tuple[tuple[str, tuple[str, ...]], ...] = (),
    location: str = "",
) -> str:
    return json.dumps(
        build_manifest(
            required_skills=skills,
            optional_skills=(),
            target_titles=titles,
            industries=(),
            location=location,
            required_credentials=credentials,
        ),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _seed_session(connection: sqlite3.Connection) -> None:
    connection.execute(
        "INSERT INTO session VALUES ('text-session','now','storage','later',120,0,0)"
    )


def _insert_brief(
    connection: sqlite3.Connection,
    *,
    brief_id: str,
    version: int,
    location: str | bytes = "",
    scoring_inputs: str | bytes | None = None,
    sealed: bool = False,
) -> None:
    connection.execute(
        "INSERT INTO role_brief "
        "(id,session_id,version,created_at,sealed_at,superseded_at,"
        "job_description,target_titles,location,industries,positive_keywords,"
        "negative_keywords,message_tone,required_experience_months,"
        "weights_version,scoring_inputs) VALUES "
        "(?,'text-session',?,'now',?,'past','job','[]',?,'[]','[\"platform\"]',"
        "'[]','plain',NULL,'1',?)",
        (
            brief_id,
            version,
            "now" if sealed else None,
            location,
            scoring_inputs if scoring_inputs is not None else _manifest(),
        ),
    )


def _insert_child(
    connection: sqlite3.Connection,
    table: str,
    *,
    brief_id: str,
    row_id: str,
    aliases: str | bytes,
) -> None:
    if table == "brief_skill":
        connection.execute(
            "INSERT INTO brief_skill VALUES (?,?,'Python','required',?,0)",
            (row_id, brief_id, aliases),
        )
    elif table == "brief_term":
        connection.execute(
            "INSERT INTO brief_term VALUES "
            "(?,?,'target_title','Engineer','engineer',?,0)",
            (row_id, brief_id, aliases),
        )
    else:
        connection.execute(
            "INSERT INTO brief_credential VALUES (?,?,'AWS','aws',?,0)",
            (row_id, brief_id, aliases),
        )


def _seed_v27_blob(
    connection: sqlite3.Connection, target: str, *, recursive_triggers: str
) -> tuple[str, str, bytes]:
    connection.execute("PRAGMA trusted_schema=OFF")
    connection.execute(f"PRAGMA recursive_triggers={recursive_triggers}")
    _seed_session(connection)
    brief_id = f"blob-{target}"
    blob = (
        b"   "
        if target == "location"
        else _manifest().encode()
        if target == "scoring_inputs"
        else b"[]"
    )
    if target == "location":
        _insert_brief(
            connection,
            brief_id=brief_id,
            version=1,
            location=blob,
            scoring_inputs=_manifest(),
        )
    elif target == "scoring_inputs":
        _insert_brief(
            connection,
            brief_id=brief_id,
            version=1,
            scoring_inputs=blob,
        )
    else:
        if target == "brief_skill":
            scoring_inputs = _manifest(skills=(("Python", ()),))
        elif target == "brief_term":
            scoring_inputs = _manifest(titles=(("Engineer", ()),))
        else:
            scoring_inputs = _manifest(credentials=(("AWS", ()),))
        _insert_brief(
            connection,
            brief_id=brief_id,
            version=1,
            scoring_inputs=scoring_inputs,
        )
        _insert_child(
            connection,
            target,
            brief_id=brief_id,
            row_id=f"child-{target}",
            aliases=blob,
        )
    connection.execute(
        "UPDATE role_brief SET sealed_at=created_at WHERE id=?", (brief_id,)
    )
    column = "aliases" if target.startswith("brief_") else target
    table = target if target.startswith("brief_") else "role_brief"
    assert connection.execute(
        f'SELECT typeof("{column}") FROM "{table}" LIMIT 1'
    ).fetchone() == ("blob",)
    return table, column, blob


def _purge_with_migration_reconciliation(path: Path) -> None:
    with _connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        for (name,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        ).fetchall():
            if name != "phase_gate_manifest_insert":
                connection.execute(f'DROP TRIGGER "{name}"')
        connection.execute("DELETE FROM session WHERE id='text-session'")


def test_exact_v27_text_database_converges_without_data_loss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "exact-v27-text.db"
    _initialize_exact_v27(path, monkeypatch)
    with _connect(path) as connection:
        _seed_session(connection)
        _insert_brief(
            connection,
            brief_id="text-brief",
            version=1,
            scoring_inputs=_manifest(),
            sealed=True,
        )
        before = connection.execute(
            "SELECT * FROM role_brief WHERE id='text-brief'"
        ).fetchone()

    upgraded = Database(path)
    upgraded.initialize()
    upgraded.dispose()
    with _connect(path) as connection:
        assert (
            connection.execute(
                "SELECT * FROM role_brief WHERE id='text-brief'"
            ).fetchone()
            == before
        )
        assert connection.execute(
            "SELECT 1 FROM schema_migration WHERE version=?",
            (v0028_m4_text_storage.VERSION,),
        ).fetchone() == (1,)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def test_v28_trigger_manifest_contains_every_storage_class_guard(
    tmp_path: Path,
) -> None:
    path = tmp_path / "guard-manifest.db"
    database = Database(path)
    database.initialize()
    database.dispose()
    with _connect(path) as connection:
        triggers = dict(
            connection.execute(
                "SELECT name,lower(sql) FROM sqlite_master "
                "WHERE type='trigger' AND name IN (?,?,?,?,?)",
                v0028_m4_text_storage.TRIGGER_NAMES,
            ).fetchall()
        )
    assert set(triggers) == set(v0028_m4_text_storage.TRIGGER_NAMES)
    for name in (
        "role_brief_text_storage_insert_v28",
        "role_brief_text_storage_update_v28",
    ):
        assert "typeof(new.location)<>'text'" in triggers[name]
        assert "typeof(new.scoring_inputs)<>'text'" in triggers[name]
    for table in ("brief_skill", "brief_term", "brief_credential"):
        assert (
            "typeof(new.aliases)<>'text'" in triggers[f"{table}_alias_text_storage_v28"]
        )


def _brief_payload() -> dict[str, Any]:
    return {
        "session_id": "session",
        "job_description": "Platform engineer",
        "required_skills": [{"term": "Python", "aliases": []}],
        "optional_skills": [],
        "target_titles": [],
        "location": "",
        "industries": [],
        "positive_keywords": [],
        "negative_keywords": [],
        "message_tone": "Direct",
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("location", b"Chicago"),
        ("term", b"Python"),
        ("aliases", [b"py"]),
    ],
)
def test_brief_api_models_reject_bytes(field: str, value: object) -> None:
    payload = _brief_payload()
    if field == "location":
        payload[field] = value
    else:
        payload["required_skills"][0][field] = value
    with pytest.raises(ValidationError):
        BriefInput.model_validate(payload)


@pytest.mark.parametrize("field", ["location", "term", "alias", "keyword"])
def test_brief_service_rejects_bytes(field: str) -> None:
    unsafe = cast(Any, b"bytes")
    value = BriefValue(
        job_description="job",
        required_skills=(
            TermValue(
                unsafe if field == "term" else "Python",
                (unsafe,) if field == "alias" else (),
            ),
        ),
        optional_skills=(),
        target_titles=(),
        location=unsafe if field == "location" else "",
        industries=(),
        positive_keywords=(unsafe,) if field == "keyword" else (),
        negative_keywords=(),
        message_tone="Direct",
    )
    with pytest.raises(ValueError, match="must be strings"):
        normalize_brief(value)


@pytest.mark.parametrize("recursive_triggers", ["ON", "OFF"])
@pytest.mark.parametrize(
    "target",
    [
        "location",
        "scoring_inputs",
        "brief_skill",
        "brief_term",
        "brief_credential",
    ],
)
def test_exact_v27_blob_fails_v28_atomically_then_purge_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recursive_triggers: str,
    target: str,
) -> None:
    path = tmp_path / f"v27-{target}-{recursive_triggers.lower()}.db"
    _initialize_exact_v27(path, monkeypatch)
    with _connect(path) as connection:
        table, column, blob = _seed_v27_blob(
            connection, target, recursive_triggers=recursive_triggers
        )
        baseline_schema = connection.execute(
            "SELECT type,name,sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ).fetchall()

    failed = Database(path)
    try:
        with pytest.raises(
            RuntimeError,
            match=rf"{table}\.{column}.*SQLite blob storage.*restore.*purge",
        ):
            failed.initialize()
    finally:
        failed.dispose()

    with _connect(path) as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM schema_migration WHERE version=?",
                (v0028_m4_text_storage.VERSION,),
            ).fetchone()
            is None
        )
        assert (
            connection.execute(
                "SELECT type,name,sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
            ).fetchall()
            == baseline_schema
        )
        assert connection.execute(
            f'SELECT "{column}" FROM "{table}" LIMIT 1'
        ).fetchone() == (blob,)

    _purge_with_migration_reconciliation(path)
    retry = Database(path)
    retry.initialize()
    retry.dispose()
    with _connect(path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM schema_migration"
        ).fetchone() == (28,)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            )
        }
        assert names.issuperset(v0028_m4_text_storage.TRIGGER_NAMES)


def _attack_role_brief(
    connection: sqlite3.Connection, target: str, operation: str, index: int
) -> None:
    baseline = f"base-{target}-{operation}-{index}"
    _insert_brief(
        connection,
        brief_id=baseline,
        version=1000 + index,
        scoring_inputs=_manifest(),
    )
    blob: bytes = b"[]" if target == "scoring_inputs" else b"   "
    if operation == "update":
        connection.execute(
            f'UPDATE role_brief SET "{target}"=? WHERE id=?', (blob, baseline)
        )
        return
    replacement = f"new-{target}-{operation}-{index}"
    row_id = baseline if operation in {"upsert", "replace"} else replacement
    prefix = "INSERT OR REPLACE" if operation == "replace" else "INSERT"
    suffix = (
        f' ON CONFLICT(id) DO UPDATE SET "{target}"=excluded."{target}"'
        if operation == "upsert"
        else ""
    )
    location: str | bytes = blob if target == "location" else ""
    scoring: str | bytes = blob if target == "scoring_inputs" else _manifest()
    connection.execute(
        f"{prefix} INTO role_brief "
        "(id,session_id,version,created_at,sealed_at,superseded_at,"
        "job_description,target_titles,location,industries,positive_keywords,"
        "negative_keywords,message_tone,required_experience_months,"
        "weights_version,scoring_inputs) VALUES "
        "(?,'text-session',?,'now',NULL,'past','job','[]',?,'[]',"
        "'[\"platform\"]','[]','plain',NULL,'1',?)"
        f"{suffix}",
        (row_id, 2000 + index, location, scoring),
    )


def _attack_aliases(
    connection: sqlite3.Connection, table: str, operation: str, index: int
) -> None:
    brief_id = f"parent-{table}-{operation}-{index}"
    _insert_brief(
        connection,
        brief_id=brief_id,
        version=3000 + index,
        scoring_inputs=_manifest(),
    )
    baseline = f"base-{table}-{operation}-{index}"
    if operation in {"update", "upsert", "replace"}:
        _insert_child(
            connection,
            table,
            brief_id=brief_id,
            row_id=baseline,
            aliases="[]",
        )
    if operation == "update":
        connection.execute(
            f"UPDATE {table} SET aliases=? WHERE id=?", (b"[]", baseline)
        )
    elif operation == "insert":
        _insert_child(
            connection,
            table,
            brief_id=brief_id,
            row_id=f"new-{table}-{operation}-{index}",
            aliases=b"[]",
        )
    else:
        if table == "brief_skill":
            values = "(?,?,'Go','required',?,1)"
            columns = "(id,brief_id,term,kind,aliases,position)"
        elif table == "brief_term":
            values = "(?,?,'Architect','architect',?,1)"
            columns = "(id,brief_id,kind,term,term_key,aliases,position)"
            values = "(?,?,'target_title','Architect','architect',?,1)"
        else:
            values = "(?,?,'GCP','gcp',?,1)"
            columns = "(id,brief_id,term,term_key,aliases,position)"
        prefix = "INSERT OR REPLACE" if operation == "replace" else "INSERT"
        suffix = (
            " ON CONFLICT(id) DO UPDATE SET aliases=excluded.aliases"
            if operation == "upsert"
            else ""
        )
        connection.execute(
            f"{prefix} INTO {table} {columns} VALUES {values}{suffix}",
            (baseline, brief_id, b"[]"),
        )


@pytest.mark.parametrize("recursive_triggers", ["ON", "OFF"])
def test_runtime_guards_reject_blob_write_matrix_and_survive_restart(
    tmp_path: Path, recursive_triggers: str
) -> None:
    path = tmp_path / f"runtime-{recursive_triggers.lower()}.db"
    database = Database(path)
    database.initialize()
    database.dispose()
    with _connect(path) as connection:
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute(f"PRAGMA recursive_triggers={recursive_triggers}")
        _seed_session(connection)
        index = 0
        for target in ("location", "scoring_inputs"):
            for operation in ("insert", "update", "upsert", "replace"):
                index += 1
                with pytest.raises(sqlite3.IntegrityError):
                    _attack_role_brief(connection, target, operation, index)
        for table in ("brief_skill", "brief_term", "brief_credential"):
            for operation in ("insert", "update", "upsert", "replace"):
                index += 1
                with pytest.raises(sqlite3.IntegrityError):
                    _attack_aliases(connection, table, operation, index)

        text_parent = "text-parent"
        _insert_brief(
            connection,
            brief_id=text_parent,
            version=9000,
            scoring_inputs=_manifest(),
        )
        for table in ("brief_skill", "brief_term", "brief_credential"):
            _insert_child(
                connection,
                table,
                brief_id=text_parent,
                row_id=f"text-{table}",
                aliases="[]",
            )
            assert connection.execute(
                f"SELECT typeof(aliases) FROM {table} WHERE id=?",
                (f"text-{table}",),
            ).fetchone() == ("text",)
        assert connection.execute(
            "SELECT typeof(location),typeof(scoring_inputs) FROM role_brief WHERE id=?",
            (text_parent,),
        ).fetchone() == ("text", "text")

    restarted = Database(path)
    restarted.initialize()
    restarted.dispose()
    with _connect(path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM role_brief "
            "WHERE typeof(location)<>'text' OR typeof(scoring_inputs)<>'text'"
        ).fetchone() == (0,)
        for table in ("brief_skill", "brief_term", "brief_credential"):
            assert connection.execute(
                f"SELECT count(*) FROM {table} WHERE typeof(aliases)<>'text'"
            ).fetchone() == (0,)
