"""Exact provenance spans.

Offsets are zero-based, half-open Unicode code-point offsets.  Python slicing
therefore verifies them directly.  The browser converts strings with
``Array.from(rawText)`` before slicing, avoiding JavaScript's UTF-16 indexing.
"""

from __future__ import annotations

from dataclasses import dataclass

_VERIFIED = object()


@dataclass(frozen=True, slots=True, init=False)
class VerifiedSpan:
    start: int
    end: int
    snippet: str

    def __init__(
        self, start: int, end: int, snippet: str, *, _token: object | None = None
    ) -> None:
        if _token is not _VERIFIED:
            raise TypeError("VerifiedSpan must be created by exact verification")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)
        object.__setattr__(self, "snippet", snippet)


def _verified(start: int, end: int, snippet: str) -> VerifiedSpan:
    return VerifiedSpan(start, end, snippet, _token=_VERIFIED)
