"""Shared deterministic Unicode normalization for scoring comparisons."""

from __future__ import annotations

import unicodedata


def caseless_nfkc(value: str) -> str:
    """Return the idempotent compatibility-caseless form of ``value``."""
    compatibility = unicodedata.normalize("NFKC", value)
    return unicodedata.normalize("NFKC", compatibility.casefold())


def normalize_text(value: str) -> str:
    """Return caseless NFKC text with canonical whitespace."""
    return " ".join(caseless_nfkc(value).split())
