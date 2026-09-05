from __future__ import annotations

import sqlite3
import unicodedata
from typing import Any

from linkedin_dashboard.db.scoring_manifest import normalize as scoring_normalize

UNICODE_CASEFOLD_COLLATION = "unicode_casefold"
SCORING_NORMALIZED_COLLATION = "scoring_normalized_v1"
SCORING_CANONICAL_COLLATION = "scoring_canonical_v1"
SCORING_CANONICAL_SENTINEL = "__linkedin_dashboard_scoring_v1_canonical__"
SCORING_DISPLAY_COLLATION = "scoring_display_v1"
SCORING_DISPLAY_CANONICAL_COLLATION = "scoring_display_canonical_v1"
SCORING_DISPLAY_CANONICAL_SENTINEL = (
    "__linkedin_dashboard_scoring_v1_display_canonical__"
)


def unicode_casefold(value: str) -> str:
    """Return the one canonical candidate-identity key used everywhere."""
    return value.casefold()


def register_unicode_casefold(connection: Any) -> None:
    """Install deterministic Unicode identity comparison on one SQLite handle."""

    def compare(left: str, right: str) -> int:
        left_key = unicode_casefold(left)
        right_key = unicode_casefold(right)
        return (left_key > right_key) - (left_key < right_key)

    connection.create_collation(UNICODE_CASEFOLD_COLLATION, compare)

    def compare_scoring(left: str, right: str) -> int:
        left_key = scoring_normalize(left)
        right_key = scoring_normalize(right)
        return (left_key > right_key) - (left_key < right_key)

    def require_canonical(left: str, right: str) -> int:
        if right == SCORING_CANONICAL_SENTINEL:
            return 0 if left == scoring_normalize(left) else 1
        if left == SCORING_CANONICAL_SENTINEL:
            return 0 if right == scoring_normalize(right) else -1
        return (left > right) - (left < right)

    def display_key(value: str) -> str:
        return " ".join(value.strip().split())

    def compare_display(left: str, right: str) -> int:
        left_key = display_key(left)
        right_key = display_key(right)
        return (left_key > right_key) - (left_key < right_key)

    def require_display_canonical(left: str, right: str) -> int:
        if right == SCORING_DISPLAY_CANONICAL_SENTINEL:
            return 0 if left == display_key(left) else 1
        if left == SCORING_DISPLAY_CANONICAL_SENTINEL:
            return 0 if right == display_key(right) else -1
        return (left > right) - (left < right)

    connection.create_collation(SCORING_NORMALIZED_COLLATION, compare_scoring)
    connection.create_collation(SCORING_CANONICAL_COLLATION, require_canonical)
    connection.create_collation(SCORING_DISPLAY_COLLATION, compare_display)
    connection.create_collation(
        SCORING_DISPLAY_CANONICAL_COLLATION, require_display_canonical
    )


def unicode_data_version() -> str:
    """Return the casefold data version that must remain bound to one database."""
    return unicodedata.unidata_version


def register_sqlite_unicode_casefold(connection: sqlite3.Connection) -> None:
    """Public helper for trusted maintenance/test connections.

    Ordinary unmanaged SQLite writers deliberately lack this collation, so
    candidate writes fail closed instead of silently reverting to ASCII-only
    identity semantics.
    """
    register_unicode_casefold(connection)
