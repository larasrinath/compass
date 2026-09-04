"""Unicode-aware deterministic term matching with exact span verification."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from linkedin_dashboard.parsing.spans import VerifiedSpan
from linkedin_dashboard.parsing.verify import verify_substring
from linkedin_dashboard.services.scoring.types import Matcher, Term

MATCHER_VERSION = "scoring-v1"
_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_STEM_EQUIVALENTS = {
    "engineer": "engineer",
    "engineering": "engineer",
    "developer": "develop",
    "development": "develop",
}


@dataclass(frozen=True, slots=True)
class TermMatch:
    matched_term: str
    matcher: Matcher
    span: VerifiedSpan


@dataclass(frozen=True, slots=True)
class _IndexedText:
    text: str
    starts: tuple[int, ...]
    ends: tuple[int, ...]


def normalize_text(value: str) -> str:
    """Canonical comparison form; never used as displayed evidence."""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _is_hangul_lead(value: str) -> bool:
    codepoint = ord(value)
    return 0x1100 <= codepoint <= 0x115F or 0xA960 <= codepoint <= 0xA97F


def _is_hangul_vowel(value: str) -> bool:
    codepoint = ord(value)
    return 0x1160 <= codepoint <= 0x11A7 or 0xD7B0 <= codepoint <= 0xD7C6


def _is_hangul_tail(value: str) -> bool:
    codepoint = ord(value)
    return 0x11A8 <= codepoint <= 0x11FF or 0xD7CB <= codepoint <= 0xD7FB


def _cluster_normalize(value: str) -> _IndexedText:
    output: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    index = 0
    while index < len(value):
        start = index
        index += 1
        if _is_hangul_lead(value[start]) and index < len(value):
            if _is_hangul_vowel(value[index]):
                index += 1
                if index < len(value) and _is_hangul_tail(value[index]):
                    index += 1
        while index < len(value) and unicodedata.combining(value[index]):
            index += 1
        cluster = value[start:index]
        normalized = unicodedata.normalize("NFKC", cluster).casefold()
        for character in normalized:
            output.append(character)
            starts.append(start)
            ends.append(index)
    return _IndexedText("".join(output), tuple(starts), tuple(ends))


def _prefix_normalize(value: str) -> _IndexedText:
    """Exact fallback for a normalization interaction crossing our clusters."""
    previous = ""
    starts: list[int] = []
    ends: list[int] = []
    for source_end in range(1, len(value) + 1):
        current = unicodedata.normalize("NFKC", value[:source_end]).casefold()
        common = 0
        limit = min(len(previous), len(current))
        while common < limit and previous[common] == current[common]:
            common += 1
        source_start = source_end - 1
        if common < len(starts):
            source_start = min(source_start, min(starts[common:]))
        starts = starts[:common]
        ends = ends[:common]
        for _ in current[common:]:
            starts.append(source_start)
            ends.append(source_end)
        previous = current
    return _IndexedText(previous, tuple(starts), tuple(ends))


def _collapse_whitespace(indexed: _IndexedText) -> _IndexedText:
    output: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    for position, character in enumerate(indexed.text):
        if character.isspace():
            if not output:
                continue
            if output[-1] == " ":
                ends[-1] = indexed.ends[position]
                continue
            character = " "
        output.append(character)
        starts.append(indexed.starts[position])
        ends.append(indexed.ends[position])
    if output and output[-1] == " ":
        output.pop()
        starts.pop()
        ends.pop()
    return _IndexedText("".join(output), tuple(starts), tuple(ends))


def _indexed_normalize(value: str) -> _IndexedText:
    indexed = _cluster_normalize(value)
    whole = unicodedata.normalize("NFKC", value).casefold()
    if indexed.text != whole:
        indexed = _prefix_normalize(value)
    if indexed.text != whole:
        raise AssertionError("indexed normalization must equal whole-string NFKC")
    return _collapse_whitespace(indexed)


def _is_word(value: str) -> bool:
    return value.isalnum()


def _whole_term(haystack: str, start: int, needle: str) -> bool:
    if not any(_is_word(value) for value in needle):
        return False
    end = start + len(needle)
    return (start == 0 or not _is_word(haystack[start - 1])) and (
        end == len(haystack) or not _is_word(haystack[end])
    )


def _literal_matches(
    raw_text: str,
    entered: str,
    matcher: Matcher,
    *,
    region_start: int,
    region_end: int,
) -> list[TermMatch]:
    region = raw_text[region_start:region_end]
    indexed = _indexed_normalize(region)
    needle = normalize_text(entered)
    if not needle:
        return []
    matches: list[TermMatch] = []
    cursor = 0
    while True:
        found = indexed.text.find(needle, cursor)
        if found < 0:
            break
        cursor = found + 1
        if not _whole_term(indexed.text, found, needle):
            continue
        start = region_start + indexed.starts[found]
        end = region_start + indexed.ends[found + len(needle) - 1]
        span = verify_substring(raw_text, raw_text[start:end], start_hint=start)
        if span is not None:
            matches.append(TermMatch(raw_text[start:end], matcher, span))
    return matches


def _stem(token: str) -> str:
    token = _STEM_EQUIVALENTS.get(token, token)
    if token.endswith("ing") and len(token) > 5:
        token = token[:-3]
        if len(token) > 2 and token[-1] == token[-2]:
            token = token[:-1]
    elif token.endswith("ed") and len(token) > 4:
        token = token[:-2]
        if len(token) > 2 and token[-1] == token[-2]:
            token = token[:-1]
    elif token.endswith("s") and not token.endswith("ss") and len(token) > 3:
        token = token[:-1]
    return _STEM_EQUIVALENTS.get(token, token)


def _stem_matches(
    raw_text: str, entered: str, *, region_start: int, region_end: int
) -> list[TermMatch]:
    normalized_entered = normalize_text(entered)
    entered_words = tuple(item.group() for item in _WORD.finditer(normalized_entered))
    if " ".join(entered_words) != normalized_entered:
        return []
    needle_tokens = tuple(_stem(item) for item in entered_words)
    if not needle_tokens:
        return []
    region = raw_text[region_start:region_end]
    indexed = _indexed_normalize(region)
    words = tuple(_WORD.finditer(indexed.text))
    matches: list[TermMatch] = []
    width = len(needle_tokens)
    for offset in range(0, len(words) - width + 1):
        window = words[offset : offset + width]
        if tuple(_stem(item.group()) for item in window) != needle_tokens:
            continue
        normalized_start = window[0].start()
        normalized_end = window[-1].end()
        start = region_start + indexed.starts[normalized_start]
        end = region_start + indexed.ends[normalized_end - 1]
        span = verify_substring(raw_text, raw_text[start:end], start_hint=start)
        if span is not None:
            matches.append(TermMatch(raw_text[start:end], Matcher.STEM, span))
    return matches


def find_term_matches(
    raw_text: str,
    term: Term,
    *,
    region_start: int = 0,
    region_end: int | None = None,
) -> tuple[TermMatch, ...]:
    """Find stable, non-overlapping exact/alias/stem hits in a text region."""
    end = len(raw_text) if region_end is None else region_end
    if region_start < 0 or end < region_start or end > len(raw_text):
        raise ValueError("invalid matching region")
    candidates = _literal_matches(
        raw_text,
        term.term,
        Matcher.EXACT,
        region_start=region_start,
        region_end=end,
    )
    for alias in term.aliases:
        candidates.extend(
            _literal_matches(
                raw_text,
                alias,
                Matcher.ALIAS,
                region_start=region_start,
                region_end=end,
            )
        )
    for entered in (term.term, *term.aliases):
        candidates.extend(
            _stem_matches(
                raw_text,
                entered,
                region_start=region_start,
                region_end=end,
            )
        )
    priority = {Matcher.EXACT: 0, Matcher.ALIAS: 1, Matcher.STEM: 2}
    candidates.sort(
        key=lambda item: (
            priority[item.matcher],
            -(item.span.end - item.span.start),
            item.span.start,
            item.span.end,
            item.matched_term.casefold(),
        )
    )
    selected: list[TermMatch] = []
    for item in candidates:
        if not any(
            item.span.start < previous.span.end and previous.span.start < item.span.end
            for previous in selected
        ):
            selected.append(item)
    return tuple(sorted(selected, key=lambda item: (item.span.start, item.span.end)))


def term_is_present(value: str, term: Term) -> bool:
    return bool(find_term_matches(value, term))


def word_tokens(value: str) -> tuple[str, ...]:
    return tuple(item.group() for item in _WORD.finditer(normalize_text(value)))
