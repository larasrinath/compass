from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from linkedin_dashboard.parsing.spans import VerifiedSpan, _verified


@dataclass(frozen=True, slots=True)
class SpanProposal:
    field_key: str
    value: str
    snippet: str
    section_name: str
    start_hint: int | None = None


@dataclass(frozen=True, slots=True)
class VerifiedProposal:
    proposal: SpanProposal
    origin: Literal["llm_verified", "llm_unverified"]
    span: VerifiedSpan | None


def verify_substring(
    raw_text: str, snippet: str, *, start_hint: int | None = None
) -> VerifiedSpan | None:
    """Return a span only after exact, code-point-indexed substring proof."""
    if not snippet:
        return None
    if start_hint is not None and start_hint >= 0:
        end = start_hint + len(snippet)
        if raw_text[start_hint:end] == snippet:
            return _verified(start_hint, end, snippet)
    start = raw_text.find(snippet)
    if start < 0:
        return None
    return _verified(start, start + len(snippet), snippet)


def verify_proposal(raw_text: str, proposal: SpanProposal) -> VerifiedProposal:
    span = verify_substring(raw_text, proposal.snippet, start_hint=proposal.start_hint)
    return VerifiedProposal(
        proposal=proposal,
        origin="llm_verified" if span is not None else "llm_unverified",
        span=span,
    )
