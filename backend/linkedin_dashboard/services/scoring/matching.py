"""Unicode-aware deterministic term matching with exact span verification."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from linkedin_dashboard.parsing.spans import VerifiedSpan
from linkedin_dashboard.parsing.verify import verify_substring
from linkedin_dashboard.services.scoring.types import Matcher, Term

MATCHER_VERSION = "scoring-v1"
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


@dataclass(frozen=True, slots=True)
class _WordSpan:
    text: str
    start: int
    end: int


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


def _hangul_kind(value: str) -> str:
    if _is_hangul_lead(value):
        return "L"
    if _is_hangul_vowel(value):
        return "V"
    if _is_hangul_tail(value):
        return "T"
    codepoint = ord(value)
    if 0xAC00 <= codepoint <= 0xD7A3:
        return "LV" if (codepoint - 0xAC00) % 28 == 0 else "LVT"
    return ""


def _hangul_continues(current: str, following: str) -> bool:
    return (
        (current == "L" and following in {"L", "V", "LV", "LVT"})
        or (current in {"LV", "V"} and following in {"V", "T"})
        or (current in {"LVT", "T"} and following == "T")
    )


def _is_mark(value: str) -> bool:
    """Treat every Unicode mark category as a continuation of its base."""
    return unicodedata.category(value).startswith("M")


def _source_clusters(value: str) -> tuple[tuple[int, int], ...]:
    clusters: list[tuple[int, int]] = []
    index = 0
    while index < len(value):
        start = index
        index += 1
        hangul_kind = _hangul_kind(value[start])
        while index < len(value):
            following_kind = _hangul_kind(value[index])
            if not _hangul_continues(hangul_kind, following_kind):
                break
            hangul_kind = following_kind
            index += 1
        while index < len(value) and _is_mark(value[index]):
            index += 1
        clusters.append((start, index))
    return tuple(clusters)


def _cluster_normalize(value: str) -> _IndexedText:
    output: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    for start, index in _source_clusters(value):
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
    return value.isalnum() or _is_mark(value)


def _word_spans(value: str) -> tuple[_WordSpan, ...]:
    """Return alphanumeric words with Unicode marks attached to their base."""
    words: list[_WordSpan] = []
    index = 0
    while index < len(value):
        if not value[index].isalnum():
            index += 1
            continue
        start = index
        index += 1
        while index < len(value) and _is_word(value[index]):
            index += 1
        words.append(_WordSpan(value[start:index], start, index))
    return tuple(words)


def _complete_raw_span(
    raw_text: str,
    indexed: _IndexedText,
    normalized_start: int,
    normalized_end: int,
    *,
    raw_offset: int,
) -> tuple[int, int] | None:
    """Map a normalized hit only when it consumes complete source expansions."""
    if normalized_start < 0 or normalized_end <= normalized_start:
        return None
    if normalized_end > len(indexed.text):
        return None
    if normalized_start and (
        indexed.starts[normalized_start - 1],
        indexed.ends[normalized_start - 1],
    ) == (indexed.starts[normalized_start], indexed.ends[normalized_start]):
        return None
    if normalized_end < len(indexed.text) and (
        indexed.starts[normalized_end - 1],
        indexed.ends[normalized_end - 1],
    ) == (indexed.starts[normalized_end], indexed.ends[normalized_end]):
        return None
    start = raw_offset + indexed.starts[normalized_start]
    end = raw_offset + indexed.ends[normalized_end - 1]
    normalized_hit = indexed.text[normalized_start:normalized_end]
    if normalize_text(raw_text[start:end]) != normalized_hit:
        return None
    return start, end


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
        raw_span = _complete_raw_span(
            raw_text,
            indexed,
            found,
            found + len(needle),
            raw_offset=region_start,
        )
        if raw_span is None:
            continue
        start, end = raw_span
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
    entered_words = _word_spans(normalized_entered)
    if " ".join(item.text for item in entered_words) != normalized_entered:
        return []
    needle_tokens = tuple(_stem(item.text) for item in entered_words)
    if not needle_tokens:
        return []
    region = raw_text[region_start:region_end]
    indexed = _indexed_normalize(region)
    words = _word_spans(indexed.text)
    matches: list[TermMatch] = []
    width = len(needle_tokens)
    for offset in range(0, len(words) - width + 1):
        window = words[offset : offset + width]
        if tuple(_stem(item.text) for item in window) != needle_tokens:
            continue
        normalized_start = window[0].start
        normalized_end = window[-1].end
        raw_span = _complete_raw_span(
            raw_text,
            indexed,
            normalized_start,
            normalized_end,
            raw_offset=region_start,
        )
        if raw_span is None:
            continue
        start, end = raw_span
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
    cluster_boundaries = {0, len(raw_text)}
    cluster_boundaries.update(
        boundary
        for start, stop in _source_clusters(raw_text)
        for boundary in (start, stop)
    )
    if region_start not in cluster_boundaries or end not in cluster_boundaries:
        return ()
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
    return tuple(item.text for item in _word_spans(normalize_text(value)))
