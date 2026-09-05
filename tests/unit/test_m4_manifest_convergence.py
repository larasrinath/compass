from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path
from typing import Any, cast

import pytest
from linkedin_dashboard.db.scoring_manifest import build_manifest, coverage_values
from linkedin_dashboard.db.session import Database
from linkedin_dashboard.db.unicode_identity import register_sqlite_unicode_casefold


def _manifest() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        build_manifest(
            required_skills=(
                (
                    "  \uff30\uff59\uff54\uff48\uff4f\uff4e  ",
                    ("Shared", "PYTHON", "Backend"),
                ),
                ("python", ("backend",)),
            ),
            optional_skills=(),
            target_titles=(
                ("Data   Engineer", ("shared", "Python", "\uff24\uff25")),
                ("data engineer", ("DE",)),
            ),
            industries=(),
            location="",
            required_credentials=(),
        ),
    )


def _insert_session(connection: sqlite3.Connection, suffix: str) -> str:
    session_id = f"manifest-session-{suffix}"
    connection.execute(
        "INSERT INTO session "
        "(id,created_at,label,purge_after,nav_budget,nav_used,send_enabled) "
        "VALUES (?, 'created', 'manifest', 'later', 120, 0, 0)",
        (session_id,),
    )
    return session_id


def _insert_unsealed_brief(
    connection: sqlite3.Connection,
    *,
    suffix: str,
    manifest: Any,
) -> tuple[str, str]:
    session_id = _insert_session(connection, suffix)
    brief_id = f"manifest-brief-{suffix}"
    connection.execute(
        "INSERT INTO role_brief "
        "(id,session_id,version,created_at,sealed_at,superseded_at,"
        "job_description,target_titles,location,industries,positive_keywords,"
        "negative_keywords,message_tone,required_experience_months,"
        "weights_version,scoring_inputs) "
        "VALUES (?,?,1,'created',NULL,NULL,'job','[]','','[]','[]','[]',"
        "'direct',24,'v1',?)",
        (
            brief_id,
            session_id,
            None
            if manifest is None
            else json.dumps(manifest, ensure_ascii=False, separators=(",", ":")),
        ),
    )
    connection.execute(
        "INSERT INTO brief_skill "
        "(id,brief_id,term,kind,aliases,position) "
        "VALUES (?,?,?,'required',?,0)",
        (
            f"skill-{suffix}",
            brief_id,
            "  \uff30\uff59\uff54\uff48\uff4f\uff4e  ",
            json.dumps(["Shared", "PYTHON", "Backend"]),
        ),
    )
    connection.execute(
        "INSERT INTO brief_skill "
        "(id,brief_id,term,kind,aliases,position) "
        "VALUES (?,?,?,'required',?,1)",
        (
            f"skill-duplicate-{suffix}",
            brief_id,
            "python",
            json.dumps(["backend"]),
        ),
    )
    connection.execute(
        "INSERT INTO brief_term "
        "(id,brief_id,kind,term,term_key,aliases,position) "
        "VALUES (?,?,'target_title',?,?,?,0)",
        (
            f"title-{suffix}",
            brief_id,
            "Data   Engineer",
            "data engineer",
            json.dumps(["shared", "Python", "\uff24\uff25"]),
        ),
    )
    return session_id, brief_id


def test_manifest_is_global_canonical_and_months_only_s3_is_empty() -> None:
    manifest = _manifest()
    assert manifest["S-1"] == [
        {
            "display": "python",
            "term": "python",
            "aliases": ["backend", "shared"],
        }
    ]
    assert manifest["S-3"] == [
        {
            "display": "Data Engineer",
            "term": "data engineer",
            "aliases": ["de", "shared"],
        },
        {"display": "python", "term": "python", "aliases": ["backend"]},
    ]
    assert coverage_values(manifest, "S-3") == (
        ("data engineer", "python"),
        ("backend", "de", "shared"),
    )
    months_only = build_manifest(
        required_skills=(),
        optional_skills=(("Go", ()),),
        target_titles=(),
        industries=(),
        location="",
        required_credentials=(),
    )
    assert months_only["S-3"] == []


@pytest.mark.parametrize("recursive", ("ON", "OFF"))
def test_database_seal_requires_child_bound_canonical_manifest(
    tmp_path: Path, recursive: str
) -> None:
    path = tmp_path / f"manifest-{recursive}.db"
    database = Database(path)
    database.initialize()
    database.dispose()

    canonical = _manifest()
    invalid: list[tuple[str, Any]] = []
    unsorted = copy.deepcopy(canonical)
    unsorted["S-3"] = list(reversed(unsorted["S-3"]))
    invalid.append(("unsorted", unsorted))
    duplicate = copy.deepcopy(canonical)
    duplicate["S-3"][0]["aliases"].append("shared")
    invalid.append(("duplicate", duplicate))
    overlap = copy.deepcopy(canonical)
    overlap["S-3"][0]["aliases"].append("python")
    invalid.append(("overlap", overlap))
    noncanonical = copy.deepcopy(canonical)
    noncanonical["S-3"][0]["term"] = (
        "\uff24\uff41\uff54\uff41 \uff25\uff4e\uff47\uff49\uff4e\uff45\uff45\uff52"
    )
    invalid.append(("noncanonical", noncanonical))
    mismatch = copy.deepcopy(canonical)
    mismatch["S-1"][0]["aliases"] = ["invented"]
    invalid.append(("child-mismatch", mismatch))
    display_mismatch = copy.deepcopy(canonical)
    display_mismatch["S-1"][0]["display"] = "pYtHoN"
    invalid.append(("display-mismatch", display_mismatch))

    with sqlite3.connect(path) as connection:
        register_sqlite_unicode_casefold(connection)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute(f"PRAGMA recursive_triggers={recursive}")

        _, valid_brief = _insert_unsealed_brief(
            connection, suffix="valid", manifest=canonical
        )
        connection.execute(
            "UPDATE role_brief SET sealed_at=created_at WHERE id=?", (valid_brief,)
        )

        months_session = _insert_session(connection, "months-only")
        months_manifest = build_manifest(
            required_skills=(),
            optional_skills=(("Go", ()),),
            target_titles=(),
            industries=(),
            location="",
            required_credentials=(),
        )
        connection.execute(
            "INSERT INTO role_brief "
            "(id,session_id,version,created_at,sealed_at,superseded_at,"
            "job_description,target_titles,location,industries,positive_keywords,"
            "negative_keywords,message_tone,required_experience_months,"
            "weights_version,scoring_inputs) VALUES "
            "('months-only',?,1,'created',NULL,NULL,'job','[]','','[]','[]','[]',"
            "'direct',24,'v1',?)",
            (
                months_session,
                json.dumps(months_manifest, separators=(",", ":")),
            ),
        )
        connection.execute(
            "INSERT INTO brief_skill "
            "(id,brief_id,term,kind,aliases,position) "
            "VALUES ('months-optional','months-only','Go','optional','[]',0)"
        )
        connection.execute(
            "UPDATE role_brief SET sealed_at=created_at WHERE id='months-only'"
        )
        assert (
            json.loads(
                connection.execute(
                    "SELECT scoring_inputs FROM role_brief WHERE id='months-only'"
                ).fetchone()[0]
            )["S-3"]
            == []
        )

        for suffix, raw_manifest in (("direct-null", None), ("malformed", "{")):
            direct_session = _insert_session(connection, suffix)
            with pytest.raises(
                sqlite3.IntegrityError, match="scoring inputs are not canonical"
            ):
                connection.execute(
                    "INSERT INTO role_brief "
                    "(id,session_id,version,created_at,sealed_at,superseded_at,"
                    "job_description,target_titles,location,industries,"
                    "positive_keywords,negative_keywords,message_tone,"
                    "required_experience_months,weights_version,scoring_inputs) "
                    "VALUES (?,?,1,'created','created',NULL,'job','[]','','[]',"
                    "'[]','[]','direct',24,'v1',?)",
                    (suffix, direct_session, raw_manifest),
                )

        for suffix, manifest in invalid:
            session_id, brief_id = _insert_unsealed_brief(
                connection, suffix=suffix, manifest=manifest
            )
            with pytest.raises(
                sqlite3.IntegrityError, match="scoring inputs are not canonical"
            ):
                connection.execute(
                    "UPDATE role_brief SET sealed_at=created_at WHERE id=?",
                    (brief_id,),
                )
            connection.execute("DELETE FROM session WHERE id=?", (session_id,))

        connection.execute("DELETE FROM session WHERE id='manifest-session-valid'")
        connection.execute("DELETE FROM session WHERE id=?", (months_session,))
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
