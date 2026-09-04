from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from linkedin_dashboard.db.scoring_manifest import build_manifest
from linkedin_dashboard.db.session import Database
from linkedin_dashboard.db.unicode_identity import register_sqlite_unicode_casefold
from linkedin_dashboard.main import create_app
from linkedin_dashboard.services import brief as brief_module
from linkedin_dashboard.services.brief import BriefService, BriefValue, TermValue
from linkedin_dashboard.settings import Settings


def _connection(path: Path, recursive: str) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    register_sqlite_unicode_casefold(connection)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(f"PRAGMA recursive_triggers={recursive}")
    connection.execute("PRAGMA trusted_schema=OFF")
    return connection


def _empty_manifest() -> dict[str, object]:
    return build_manifest(
        required_skills=(),
        optional_skills=(),
        target_titles=(),
        industries=(),
        location="",
        required_credentials=(),
    )


def _role_values(
    suffix: str, manifest: str | None, *, sealed: bool
) -> tuple[object, ...]:
    return (
        f"brief-{suffix}",
        f"session-{suffix}",
        1,
        "now",
        "now" if sealed else None,
        None,
        "job",
        "[]",
        "",
        "[]",
        "[]",
        "[]",
        "plain",
        None,
        "v1",
        manifest,
    )


def _insert_session(connection: sqlite3.Connection, suffix: str) -> None:
    connection.execute(
        "INSERT INTO session VALUES (?, 'now', 'manifest', 'later', 120, 0, 0)",
        (f"session-{suffix}",),
    )


_ROLE_INSERT = "INSERT INTO role_brief VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"


def _invalid_manifests() -> tuple[str | None, ...]:
    compact = json.dumps(_empty_manifest(), separators=(",", ":"))
    duplicate_top = (
        '{"matcher_version":"scoring-v1","S-1":[],"S-1":[],"S-2":[],'
        '"S-3":[],"S-4":[],"S-5":[],"S-6":[]}'
    )
    extra_top = compact[:-1] + ',"unexpected":[]}'
    nested_duplicate = compact.replace(
        '"S-1":[]',
        '"S-1":[{"display":"Python","term":"python","term":"python","aliases":[]}]',
        1,
    )
    nested_extra = compact.replace(
        '"S-1":[]',
        '"S-1":[{"display":"Python","term":"python","aliases":[],"unexpected":true}]',
        1,
    )
    nested_missing = compact.replace(
        '"S-1":[]',
        '"S-1":[{"display":"Python","term":"python"}]',
        1,
    )
    nested_null = compact.replace(
        '"S-1":[]',
        '"S-1":[{"display":"Python","term":"python","aliases":null}]',
        1,
    )
    return (
        duplicate_top,
        extra_top,
        nested_duplicate,
        nested_extra,
        nested_missing,
        nested_null,
        None,
        "null",
        "{",
    )


@pytest.mark.parametrize("recursive", ("ON", "OFF"))
@pytest.mark.parametrize("operation", ("insert", "update", "upsert", "replace"))
def test_manifest_shape_is_total_for_every_seal_path(
    tmp_path: Path, recursive: str, operation: str
) -> None:
    path = tmp_path / f"manifest-{recursive}-{operation}.db"
    database = Database(path)
    database.initialize()
    database.dispose()

    with _connection(path, recursive) as connection:
        for index, manifest in enumerate(_invalid_manifests()):
            suffix = f"{operation}-{recursive}-{index}"
            _insert_session(connection, suffix)
            values = _role_values(suffix, manifest, sealed=operation != "update")
            with pytest.raises(
                sqlite3.IntegrityError,
                match="scoring inputs are not canonical",
            ):
                if operation == "update":
                    connection.execute(_ROLE_INSERT, (*values[:4], None, *values[5:]))
                    connection.execute(
                        "UPDATE role_brief SET sealed_at=created_at WHERE id=?",
                        (f"brief-{suffix}",),
                    )
                elif operation == "upsert":
                    connection.execute(
                        _ROLE_INSERT
                        + " ON CONFLICT(id) DO UPDATE SET sealed_at=excluded.sealed_at",
                        values,
                    )
                elif operation == "replace":
                    connection.execute(
                        _ROLE_INSERT.replace("INSERT", "INSERT OR REPLACE", 1), values
                    )
                else:
                    connection.execute(_ROLE_INSERT, values)


def _insert_unsealed_brief(
    connection: sqlite3.Connection,
    suffix: str,
    manifest: dict[str, object] | None = None,
) -> str:
    _insert_session(connection, suffix)
    brief_id = f"brief-{suffix}"
    connection.execute(
        _ROLE_INSERT,
        _role_values(
            suffix,
            json.dumps(manifest or _empty_manifest(), separators=(",", ":")),
            sealed=False,
        ),
    )
    return brief_id


def _child_insert(
    connection: sqlite3.Connection,
    table: str,
    suffix: str,
    brief_id: str,
    term: str,
    aliases: str,
) -> None:
    if table == "brief_skill":
        connection.execute(
            "INSERT INTO brief_skill VALUES (?,?,?,?,?,0)",
            (f"child-{suffix}", brief_id, term, "required", aliases),
        )
    elif table == "brief_term":
        connection.execute(
            "INSERT INTO brief_term VALUES (?,?,?,?,?,?,0)",
            (
                f"child-{suffix}",
                brief_id,
                "target_title",
                term,
                term.casefold(),
                aliases,
            ),
        )
    else:
        connection.execute(
            "INSERT INTO brief_credential VALUES (?,?,?,?,?,0)",
            (f"child-{suffix}", brief_id, term, term.casefold(), aliases),
        )


@pytest.mark.parametrize("recursive", ("ON", "OFF"))
def test_every_child_alias_source_rejects_non_arrays_and_non_strings(
    tmp_path: Path, recursive: str
) -> None:
    path = tmp_path / f"child-shapes-{recursive}.db"
    database = Database(path)
    database.initialize()
    database.dispose()
    invalid_aliases = ("{}", "1", "null", '[["nested"]]', "[true]", "[")

    with _connection(path, recursive) as connection:
        for table in ("brief_skill", "brief_term", "brief_credential"):
            for index, aliases in enumerate(invalid_aliases):
                suffix = f"{table}-{recursive}-{index}"
                brief_id = _insert_unsealed_brief(connection, suffix)
                with pytest.raises(sqlite3.IntegrityError, match="source is invalid"):
                    _child_insert(
                        connection, table, suffix, brief_id, "Python", aliases
                    )


@pytest.mark.parametrize("recursive", ("ON", "OFF"))
@pytest.mark.parametrize("table", ("brief_skill", "brief_term", "brief_credential"))
@pytest.mark.parametrize("collision", ("primary", "owner"))
def test_child_alias_collisions_use_nfkc_casefold_identity(
    tmp_path: Path, recursive: str, table: str, collision: str
) -> None:
    path = tmp_path / f"collision-{table}-{collision}-{recursive}.db"
    database = Database(path)
    database.initialize()
    database.dispose()

    with _connection(path, recursive) as connection:
        brief_id = _insert_unsealed_brief(connection, "collision")
        first_aliases = "[]" if collision == "primary" else '["Shared"]'
        _child_insert(connection, table, "first", brief_id, "Python", first_aliases)
        aliases = (
            '["\uff30\uff39\uff34\uff28\uff2f\uff2e"]'
            if collision == "primary"
            else '["\uff33\uff28\uff21\uff32\uff25\uff24"]'
        )
        with pytest.raises(sqlite3.IntegrityError, match="source is invalid"):
            _child_insert(connection, table, "second", brief_id, "Rust", aliases)


@pytest.mark.parametrize("recursive", ("ON", "OFF"))
@pytest.mark.parametrize("table", ("brief_skill", "brief_term", "brief_credential"))
@pytest.mark.parametrize("aliases", ('["valid"]', "{}"))
def test_child_replace_cannot_bypass_validation_or_immutability(
    tmp_path: Path, recursive: str, table: str, aliases: str
) -> None:
    path = tmp_path / f"replace-{table}-{recursive}.db"
    database = Database(path)
    database.initialize()
    database.dispose()

    with _connection(path, recursive) as connection:
        brief_id = _insert_unsealed_brief(connection, "replace")
        _child_insert(connection, table, "same", brief_id, "Python", "[]")
        before = connection.execute(
            f"SELECT term,aliases FROM {table} WHERE id='child-same'"
        ).fetchone()
        with pytest.raises(sqlite3.IntegrityError, match="already exists"):
            if table == "brief_skill":
                connection.execute(
                    "INSERT OR REPLACE INTO brief_skill VALUES "
                    "('child-same',?,'Rust','required',?,0)",
                    (brief_id, aliases),
                )
            elif table == "brief_term":
                connection.execute(
                    "INSERT OR REPLACE INTO brief_term VALUES "
                    "('child-same',?,'target_title','Rust','rust',?,0)",
                    (brief_id, aliases),
                )
            else:
                connection.execute(
                    "INSERT OR REPLACE INTO brief_credential VALUES "
                    "('child-same',?,'Rust','rust',?,0)",
                    (brief_id, aliases),
                )
        assert (
            connection.execute(
                f"SELECT term,aliases FROM {table} WHERE id='child-same'"
            ).fetchone()
            == before
        )


@pytest.mark.parametrize("recursive", ("ON", "OFF"))
@pytest.mark.parametrize("table", ("brief_term", "brief_credential"))
def test_child_replace_cannot_reuse_a_canonical_key(
    tmp_path: Path, recursive: str, table: str
) -> None:
    path = tmp_path / f"replace-key-{table}-{recursive}.db"
    database = Database(path)
    database.initialize()
    database.dispose()

    with _connection(path, recursive) as connection:
        brief_id = _insert_unsealed_brief(connection, "replace-key")
        _child_insert(connection, table, "first", brief_id, "Python", "[]")
        with pytest.raises(sqlite3.IntegrityError, match="already exists"):
            if table == "brief_term":
                connection.execute(
                    "INSERT OR REPLACE INTO brief_term VALUES "
                    "('child-second',?,'target_title','Rust','python','[]',0)",
                    (brief_id,),
                )
            else:
                connection.execute(
                    "INSERT OR REPLACE INTO brief_credential VALUES "
                    "('child-second',?,'Rust','python','[]',0)",
                    (brief_id,),
                )
        assert connection.execute(
            f"SELECT id,term FROM {table} WHERE brief_id=?", (brief_id,)
        ).fetchall() == [("child-first", "Python")]


def test_api_rejects_alias_collisions_without_writing_a_brief(tmp_path: Path) -> None:
    app = create_app(
        Settings(db_path=tmp_path / "api-collision.db", llm_provider="null")
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        session_id = client.post("/api/session", json={"label": "collision"}).json()[
            "id"
        ]
        response = client.post(
            "/api/briefs",
            json={
                "session_id": session_id,
                "job_description": "Platform engineer",
                "required_skills": [
                    {"term": "Python", "aliases": ["Shared"]},
                    {
                        "term": "Rust",
                        "aliases": ["\uff33\uff28\uff21\uff32\uff25\uff24"],
                    },
                ],
                "optional_skills": [],
                "target_titles": [],
                "location": "",
                "industries": [],
                "positive_keywords": [],
                "negative_keywords": [],
                "message_tone": "Direct",
            },
        )
        assert response.status_code == 422
        assert "Shared" not in response.text
        current = client.get("/api/briefs/current", params={"session_id": session_id})
        assert current.status_code == 200
        assert current.json() is None


def test_api_rejects_pathological_vocabulary_before_database_write(
    tmp_path: Path,
) -> None:
    app = create_app(Settings(db_path=tmp_path / "api-budget.db", llm_provider="null"))
    required_skills = [
        {
            "term": f"term-{term}",
            "aliases": [f"alias-{term}-{alias}" for alias in range(30)],
        }
        for term in range(17)
    ]
    with TestClient(app, base_url="http://127.0.0.1") as client:
        session_id = client.post("/api/session", json={"label": "budget"}).json()["id"]
        response = client.post(
            "/api/briefs",
            json={
                "session_id": session_id,
                "job_description": "Platform engineer",
                "required_skills": required_skills,
                "optional_skills": [],
                "target_titles": [],
                "location": "",
                "industries": [],
                "positive_keywords": [],
                "negative_keywords": [],
                "message_tone": "Direct",
            },
        )
        assert response.status_code == 422
        assert "alias-16-29" not in response.text
        with app.state.database.engine.connect() as connection:
            assert (
                connection.exec_driver_sql(
                    "SELECT count(*) FROM role_brief WHERE session_id=?", (session_id,)
                ).scalar_one()
                == 0
            )


@pytest.mark.parametrize("recursive", ("ON", "OFF"))
def test_database_rejects_pathological_vocabulary_during_child_insert(
    tmp_path: Path, recursive: str
) -> None:
    path = tmp_path / f"db-budget-{recursive}.db"
    database = Database(path)
    database.initialize()
    database.dispose()
    aliases = [f"alias-{term}-{alias}" for term in range(17) for alias in range(30)]

    with _connection(path, recursive) as connection:
        brief_id = _insert_unsealed_brief(connection, "budget")
        for term in range(16):
            _child_insert(
                connection,
                "brief_skill",
                f"budget-{term}",
                brief_id,
                f"term-{term}",
                json.dumps(aliases[term * 30 : (term + 1) * 30]),
            )
        with pytest.raises(sqlite3.IntegrityError, match="source is invalid"):
            _child_insert(
                connection,
                "brief_skill",
                "budget-16",
                brief_id,
                "term-16",
                json.dumps(aliases[16 * 30 :]),
            )


def test_expensive_manifest_preparation_does_not_hold_transition_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = Database(tmp_path / "concurrency.db")
    database.initialize()
    service = BriefService(database)
    session_id = service.create_session("first").id
    entered = threading.Event()
    release = threading.Event()
    original = brief_module.scoring_inputs

    def delayed(value: BriefValue) -> dict[str, object]:
        entered.set()
        assert release.wait(5)
        return original(value)

    monkeypatch.setattr(brief_module, "scoring_inputs", delayed)
    value = BriefValue(
        job_description="Platform engineer",
        required_skills=(TermValue("Python"),),
        optional_skills=(),
        target_titles=(),
        location="",
        industries=(),
        positive_keywords=(),
        negative_keywords=(),
        message_tone="Direct",
    )
    saved: list[Any] = []
    thread = threading.Thread(
        target=lambda: saved.append(service.save(session_id, value))
    )
    thread.start()
    assert entered.wait(2)
    second = service.create_session("second")
    assert second.id
    release.set()
    thread.join(5)
    database.dispose()
    assert not thread.is_alive()
    assert len(saved) == 1


def test_maximum_supported_manifest_has_bounded_seal_cost(tmp_path: Path) -> None:
    path = tmp_path / "bounded-seal.db"
    database = Database(path)
    database.initialize()
    database.dispose()
    aliases = tuple(
        f"alias-{term}-{index}" for term in range(16) for index in range(30)
    )
    terms = tuple(
        (f"term-{term}", aliases[term * 30 : (term + 1) * 30]) for term in range(16)
    )
    manifest = build_manifest(
        required_skills=terms,
        optional_skills=(),
        target_titles=(),
        industries=(),
        location="",
        required_credentials=(),
    )

    with _connection(path, "OFF") as connection:
        brief_id = _insert_unsealed_brief(connection, "bounded", manifest)
        for position, (term, term_aliases) in enumerate(terms):
            connection.execute(
                "INSERT INTO brief_skill VALUES (?,?,?,?,?,?)",
                (
                    f"bounded-{position}",
                    brief_id,
                    term,
                    "required",
                    json.dumps(term_aliases),
                    position,
                ),
            )
        started = time.perf_counter()
        connection.execute(
            "UPDATE role_brief SET sealed_at=created_at WHERE id=?", (brief_id,)
        )
        large_elapsed = time.perf_counter() - started

        small_id = _insert_unsealed_brief(connection, "small")
        started = time.perf_counter()
        connection.execute(
            "UPDATE role_brief SET sealed_at=created_at WHERE id=?", (small_id,)
        )
        small_elapsed = time.perf_counter() - started

    # A ratio plus a wide scheduling allowance catches the former superlinear
    # multi-second seal without turning ordinary CI jitter into a failure.
    assert large_elapsed <= small_elapsed * 100 + 1.0
