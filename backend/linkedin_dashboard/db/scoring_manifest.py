"""Frozen canonical representation for scoring-v1 brief inputs."""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

MATCHER_VERSION = "scoring-v1"
SIGNAL_IDS = ("S-1", "S-2", "S-3", "S-4", "S-5", "S-6", "S-8")
MAX_TERM_LENGTH = 240
MAX_ALIAS_LENGTH = 160
MAX_ALIASES_PER_TERM = 30
MAX_BRIEF_VOCABULARY = 512
MAX_BRIEF_CANONICAL_CHARS = 32_768

type RawTerm = tuple[str, Sequence[str]]


def normalize(value: str) -> str:
    compatibility = unicodedata.normalize("NFKC", value)
    caseless = unicodedata.normalize("NFKC", compatibility.casefold())
    return " ".join(caseless.split())


def canonical_entries(values: Iterable[RawTerm]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, set[str]]] = {}
    for index, raw_entry in enumerate(values):
        if index >= MAX_BRIEF_VOCABULARY:
            raise ValueError("brief has too many term entries")
        if type(raw_entry) not in (tuple, list) or len(raw_entry) != 2:
            raise ValueError("brief term entries must be two-item sequences")
        raw_term, raw_aliases = raw_entry
        if type(raw_term) is not str:
            raise ValueError("brief terms must be strings")
        if "\x00" in raw_term:
            raise ValueError("brief terms cannot contain NUL")
        if type(raw_aliases) not in (tuple, list):
            raise ValueError("brief aliases must be sequences of strings")
        if len(raw_aliases) > MAX_ALIASES_PER_TERM:
            raise ValueError("brief terms have too many aliases")
        display = " ".join(raw_term.strip().split())
        key = normalize(display)
        if not key:
            raise ValueError("brief terms must be canonicalizable")
        if len(raw_term) > MAX_TERM_LENGTH or len(key) > MAX_TERM_LENGTH:
            raise ValueError("brief terms exceed the supported length")
        aliases = tuple(raw_aliases)
        group = grouped.setdefault(key, {"displays": set(), "aliases": set()})
        group["displays"].add(display)
        for alias in aliases:
            if type(alias) is not str:
                raise ValueError("brief aliases must be strings")
            if "\x00" in alias:
                raise ValueError("brief aliases cannot contain NUL")
            alias_key = normalize(alias)
            if not alias_key:
                raise ValueError("brief aliases must be canonicalizable")
            if len(alias) > MAX_ALIAS_LENGTH or len(alias_key) > MAX_ALIAS_LENGTH:
                raise ValueError("brief aliases exceed the supported length")
            if alias_key != key:
                group["aliases"].add(alias_key)

    primary_keys = set(grouped)
    alias_owner: dict[str, str] = {}
    for primary in sorted(grouped):
        for alias in grouped[primary]["aliases"]:
            if alias in primary_keys:
                raise ValueError("an alias cannot equal another primary term")
            previous = alias_owner.get(alias)
            if previous is not None and previous != primary:
                raise ValueError("an alias cannot belong to multiple primary terms")
            alias_owner[alias] = primary

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


def _coverage_entries(
    *categories: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge independently valid categories for ownerless S-3 coverage.

    The scoring kernel searches the union of target titles and required skills.
    Alias ownership across those two categories is deliberately irrelevant: the
    absence record stores one sorted vocabulary.  We retain deterministic entry
    ownership in the frozen JSON only so the existing manifest shape stays small.
    """

    grouped: dict[str, dict[str, set[str]]] = {}
    aliases: set[str] = set()
    owners: dict[str, set[str]] = {}
    for category in categories:
        for entry in category:
            key = str(entry["term"])
            group = grouped.setdefault(key, {"displays": set(), "aliases": set()})
            group["displays"].add(str(entry["display"]))
            for alias in entry["aliases"]:
                alias_key = str(alias)
                aliases.add(alias_key)
                owners.setdefault(alias_key, set()).add(key)
    aliases.difference_update(grouped)
    for alias in sorted(aliases):
        # This association has no scoring meaning.  Selecting the first existing
        # source owner merely gives the ownerless set a stable JSON representation.
        owner = min(owners[alias])
        grouped[owner]["aliases"].add(alias)
    return [
        {
            "display": min(grouped[key]["displays"]),
            "term": key,
            "aliases": sorted(grouped[key]["aliases"]),
        }
        for key in sorted(grouped)
    ]


def validate_manifest_budget(entries: Iterable[Sequence[dict[str, Any]]]) -> None:
    vocabulary = 0
    canonical_chars = 0
    for category in entries:
        for entry in category:
            vocabulary += 1 + len(entry["aliases"])
            canonical_chars += len(entry["term"]) + sum(
                len(alias) for alias in entry["aliases"]
            )
    if vocabulary > MAX_BRIEF_VOCABULARY:
        raise ValueError("brief scoring vocabulary is too large")
    if canonical_chars > MAX_BRIEF_CANONICAL_CHARS:
        raise ValueError("brief scoring vocabulary text is too large")


def build_manifest(
    *,
    required_skills: Iterable[RawTerm],
    optional_skills: Iterable[RawTerm],
    target_titles: Iterable[RawTerm],
    industries: Iterable[RawTerm],
    location: str,
    required_credentials: Iterable[RawTerm],
) -> dict[str, object]:
    if type(location) is not str:
        raise ValueError("brief location must be a string")
    if "\x00" in location:
        raise ValueError("brief location cannot contain NUL")
    location_values: tuple[RawTerm, ...] = (
        ((location, ()),) if normalize(location) else ()
    )
    required_entries = canonical_entries(required_skills)
    optional_entries = canonical_entries(optional_skills)
    title_entries = canonical_entries(target_titles)
    industry_entries = canonical_entries(industries)
    location_entries = canonical_entries(location_values)
    credential_entries = canonical_entries(required_credentials)
    validate_manifest_budget(
        (
            required_entries,
            optional_entries,
            title_entries,
            industry_entries,
            location_entries,
            credential_entries,
        )
    )
    return {
        "matcher_version": MATCHER_VERSION,
        "S-1": required_entries,
        "S-2": optional_entries,
        "S-3": _coverage_entries(title_entries, required_entries),
        "S-4": title_entries,
        "S-5": industry_entries,
        "S-6": location_entries,
        "S-8": credential_entries,
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


def expected_claim_labels(
    manifest: Mapping[str, Any], signal_id: str, months: int | None
) -> dict[str, tuple[str, ...]]:
    """Canonical claim identities; title matches may name one selected target.

    The tuple's first value is the aggregate display for non-matched scalar
    claims. Remaining values are allowed selected target displays for S-4.
    This boundary describes immutable brief identity, not scoring behavior.
    """
    entries = manifest[signal_id]
    if signal_id in {"S-1", "S-2", "S-8"}:
        return {
            f"{signal_id}:{entry['term']}": (entry["display"],) for entry in entries
        }
    if signal_id == "S-3":
        return (
            {"S-3:experience-depth": (f"{months} months relevant experience",)}
            if months is not None and months > 0
            else {}
        )
    if not entries:
        return {}
    displays = tuple(entry["display"] for entry in entries)
    if signal_id == "S-4":
        return {"S-4:title-similarity": (", ".join(displays), *displays)}
    if signal_id == "S-5":
        return {"S-5:industry-relevance": (", ".join(displays),)}
    if signal_id == "S-6":
        return {"S-6:location-fit": displays}
    raise ValueError("unsupported scoring signal")


def claim_label_matches(
    expected: Mapping[str, tuple[str, ...]],
    signal_id: str,
    claim_key: str,
    display_term: str,
    verdict: str,
) -> bool:
    labels = expected.get(claim_key, ())
    if signal_id == "S-4":
        labels = labels[1:] if verdict == "matched" else labels[:1]
    return display_term in labels
