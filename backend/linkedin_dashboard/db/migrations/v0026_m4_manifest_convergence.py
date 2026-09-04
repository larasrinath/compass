"""Converge databases that recorded the rejected v0025 manifest shape."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Connection

from linkedin_dashboard.db.migrations import v0025_m4_semantic_integrity as v25
from linkedin_dashboard.db.scoring_manifest import normalize

VERSION = "0026_m4_manifest_convergence"


def _legacy_entry(term: str, aliases: list[str]) -> dict[str, object]:
    return {
        "display": term,
        "term": normalize(term),
        "aliases": sorted(key for alias in aliases if (key := normalize(alias))),
    }


def _rejected_manifest(
    connection: Connection, brief_id: str, location: str
) -> dict[str, object]:
    skills: dict[str, list[dict[str, object]]] = {"required": [], "optional": []}
    for term, kind, aliases in connection.exec_driver_sql(
        "SELECT term,kind,aliases FROM brief_skill WHERE brief_id=? "
        "ORDER BY position,id",
        (brief_id,),
    ):
        skills[str(kind)].append(_legacy_entry(str(term), v25._array(aliases)))
    terms: dict[str, list[dict[str, object]]] = {
        "target_title": [],
        "industry": [],
    }
    for term, kind, aliases in connection.exec_driver_sql(
        "SELECT term,kind,aliases FROM brief_term WHERE brief_id=? "
        "ORDER BY position,id",
        (brief_id,),
    ):
        terms[str(kind)].append(_legacy_entry(str(term), v25._array(aliases)))
    credentials = [
        _legacy_entry(str(term), v25._array(aliases))
        for term, aliases in connection.exec_driver_sql(
            "SELECT term,aliases FROM brief_credential WHERE brief_id=? "
            "ORDER BY position,id",
            (brief_id,),
        )
    ]
    location_key = normalize(location)
    location_entries = (
        [{"display": location, "term": location_key, "aliases": []}]
        if location_key
        else []
    )
    return {
        "matcher_version": "scoring-v1",
        "S-1": skills["required"],
        "S-2": skills["optional"],
        "S-3": [*terms["target_title"], *skills["required"]],
        "S-4": terms["target_title"],
        "S-5": terms["industry"],
        "S-6": location_entries,
        "S-8": credentials,
    }


def _decode(value: Any, brief_id: str) -> object:
    try:
        return json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError) as error:
        raise RuntimeError(
            f"cannot apply {VERSION}: brief {brief_id} has invalid scoring inputs"
        ) from error


def _converge_rejected_manifests(connection: Connection) -> None:
    for brief_id, location, stored in connection.exec_driver_sql(
        "SELECT id,location,scoring_inputs FROM role_brief"
    ):
        key = str(brief_id)
        actual = _decode(stored, key)
        expected = v25._brief_manifest(connection, key, str(location))
        if actual == expected:
            continue
        rejected = _rejected_manifest(connection, key, str(location))
        if actual != rejected:
            raise RuntimeError(
                f"cannot apply {VERSION}: brief {key} scoring inputs match neither "
                "the rejected v25 shape nor its canonical immutable terms"
            )
        connection.exec_driver_sql(
            "UPDATE role_brief SET scoring_inputs=? WHERE id=?",
            (
                json.dumps(
                    expected,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                key,
            ),
        )


def apply(connection: Connection) -> None:
    for name in (
        "role_brief_append_only",
        "role_brief_scoring_insert_v25",
        "role_brief_scoring_seal_v25",
        "score_claim_finalize_v25",
        "score_finalize_signal_set_v25",
        "signal_coverage_shape_v25",
    ):
        connection.exec_driver_sql(f'DROP TRIGGER IF EXISTS "{name}"')
    connection.exec_driver_sql("DROP INDEX IF EXISTS score_signal_identity_v25")
    _converge_rejected_manifests(connection)
    v25._prepare_brief_manifests(connection)
    v25._preflight(connection)
    for statement in v25.STATEMENTS:
        connection.exec_driver_sql(statement)
