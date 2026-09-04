"""Frozen canonical representation for scoring-v1 brief inputs."""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

MATCHER_VERSION = "scoring-v1"
SIGNAL_IDS = ("S-1", "S-2", "S-3", "S-4", "S-5", "S-6", "S-8")

type RawTerm = tuple[str, Sequence[str]]


def normalize(value: str) -> str:
    compatibility = unicodedata.normalize("NFKC", value)
    caseless = unicodedata.normalize("NFKC", compatibility.casefold())
    return " ".join(caseless.split())


def canonical_entries(values: Iterable[RawTerm]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, set[str]]] = {}
    for raw_term, raw_aliases in values:
        display = " ".join(raw_term.strip().split())
        key = normalize(display)
        if not key:
            continue
        group = grouped.setdefault(key, {"displays": set(), "aliases": set()})
        group["displays"].add(display)
        group["aliases"].update(
            alias_key for alias in raw_aliases if (alias_key := normalize(alias))
        )

    primary_keys = set(grouped)
    alias_owner: dict[str, str] = {}
    for primary in sorted(grouped):
        for alias in grouped[primary]["aliases"]:
            if alias not in primary_keys:
                alias_owner.setdefault(alias, primary)

    return [
        {
            "display": min(grouped[key]["displays"]),
            "term": key,
            "aliases": sorted(
                alias for alias, owner in alias_owner.items() if owner == key
            ),
        }
        for key in sorted(grouped)
    ]


def build_manifest(
    *,
    required_skills: Iterable[RawTerm],
    optional_skills: Iterable[RawTerm],
    target_titles: Iterable[RawTerm],
    industries: Iterable[RawTerm],
    location: str,
    required_credentials: Iterable[RawTerm],
) -> dict[str, object]:
    required = tuple(required_skills)
    optional = tuple(optional_skills)
    titles = tuple(target_titles)
    industry_values = tuple(industries)
    credentials = tuple(required_credentials)
    location_values: tuple[RawTerm, ...] = (
        ((location, ()),) if normalize(location) else ()
    )
    return {
        "matcher_version": MATCHER_VERSION,
        "S-1": canonical_entries(required),
        "S-2": canonical_entries(optional),
        "S-3": canonical_entries((*titles, *required)),
        "S-4": canonical_entries(titles),
        "S-5": canonical_entries(industry_values),
        "S-6": canonical_entries(location_values),
        "S-8": canonical_entries(credentials),
    }


def coverage_values(
    manifest: Mapping[str, object],
    signal_id: str,
    *,
    term_key: str | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    raw_entries = manifest.get(signal_id)
    if not isinstance(raw_entries, list):
        raise ValueError("brief scoring inputs are unavailable")
    entries = [
        entry
        for entry in raw_entries
        if isinstance(entry, dict)
        and (term_key is None or entry.get("term") == term_key)
    ]
    if len(entries) != len(raw_entries) and term_key is None:
        raise ValueError("brief scoring inputs are malformed")
    terms = tuple(
        entry["term"] for entry in entries if isinstance(entry.get("term"), str)
    )
    aliases = tuple(
        sorted(
            {
                alias
                for entry in entries
                for alias in entry.get("aliases", [])
                if isinstance(alias, str)
            }
        )
    )
    if not terms or len(terms) != len(entries):
        raise ValueError("absence coverage requires canonical search terms")
    return terms, aliases


def canonical_coverage_values(
    terms: Iterable[str], aliases: Iterable[str]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    canonical_terms = tuple(
        sorted({key for value in terms if (key := normalize(value))})
    )
    term_set = set(canonical_terms)
    canonical_aliases = tuple(
        sorted(
            {
                key
                for value in aliases
                if (key := normalize(value)) and key not in term_set
            }
        )
    )
    return canonical_terms, canonical_aliases
