from __future__ import annotations

import sqlite3
import unicodedata
from typing import Any

UNICODE_CASEFOLD_COLLATION = "unicode_casefold"


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
