from __future__ import annotations

import re

from linkedin_dashboard.parsing.common import (
    LocatedLine,
    ParsedValue,
    located_lines,
    value,
)

_DURATION_PART = r"\d+\s+(?:mos?|yrs?)"
_DURATION_LINE = re.compile(rf"{_DURATION_PART}(?:\s+{_DURATION_PART})*", re.IGNORECASE)
_YEAR = r"(?:19|20)\d{2}"
_MONTH_YEAR = rf"(?:[^\W\d_]+\.?\s+)?{_YEAR}"
_ROLE_DATE_LINE = re.compile(
    rf"{_MONTH_YEAR}\s*[-\u2013\u2014]\s*(?:{_MONTH_YEAR}|present|current)"
    rf"(?:\s*(?:[·•,]\s*)?{_DURATION_PART}(?:\s+{_DURATION_PART})*)?",
    re.IGNORECASE,
)
_SINGLE_YEAR_LINE = re.compile(_YEAR)


def _is_group_duration(text: str) -> bool:
    """Identify the duration-only anchor used by grouped employment headers."""
    return _DURATION_LINE.fullmatch(text) is not None


def _is_role_date(text: str) -> bool:
    """Accept complete date lines, never titles or prose that merely mention a year."""
    return (
        _ROLE_DATE_LINE.fullmatch(text) is not None
        or _SINGLE_YEAR_LINE.fullmatch(text) is not None
        or _is_group_duration(text)
    )


def _has_strong_gap(raw_text: str, previous: LocatedLine, current: LocatedLine) -> bool:
    gap = raw_text[previous.start + len(previous.text) : current.start]
    return gap.count("\n") >= 3 or ("\n" not in gap and gap.count("\r") >= 3)


def _append_entry(
    raw_text: str,
    output: list[ParsedValue],
    entry: int,
    title: LocatedLine,
    company: LocatedLine | None,
    dates: LocatedLine | None,
) -> None:
    for key, line in (("title", title), ("company", company), ("dates", dates)):
        if line is not None:
            parsed = value(raw_text, f"experience.{entry}.{key}", line)
            if parsed is not None:
                output.append(parsed)


def _parse_without_date_anchors(
    raw_text: str, lines: list[LocatedLine]
) -> list[ParsedValue]:
    """Keep the conservative legacy fallback for sections with no role dates."""
    output: list[ParsedValue] = []
    for entry, position in enumerate(range(0, len(lines), 2)):
        company = lines[position + 1] if position + 1 < len(lines) else None
        _append_entry(raw_text, output, entry, lines[position], company, None)
    return output


def parse(raw_text: str) -> list[ParsedValue]:
    """Parse date-anchored roles and inherit company names for grouped roles."""
    try:
        lines = located_lines(raw_text, headings={"experience"})
        anchors: list[tuple[list[LocatedLine], LocatedLine, bool]] = []
        pending: list[LocatedLine] = []
        pending_starts_at_boundary = False
        previous: LocatedLine | None = None
        for line in lines:
            strong_gap = previous is not None and _has_strong_gap(
                raw_text, previous, line
            )
            if _is_role_date(line.text):
                anchors.append((pending, line, pending_starts_at_boundary))
                pending = []
                pending_starts_at_boundary = False
            else:
                if strong_gap:
                    pending = []
                    pending_starts_at_boundary = True
                pending.append(line)
            previous = line

        if not anchors:
            return _parse_without_date_anchors(raw_text, lines)

        output: list[ParsedValue] = []
        entry = 0
        group_company: LocatedLine | None = None
        for candidates, dates, starts_at_boundary in anchors:
            if not candidates:
                continue

            if _is_group_duration(dates.text):
                # Tail lines from the preceding role may share ordinary blank
                # spacing with a new group header. The duration-only anchor
                # makes its immediately preceding line the parent company.
                group_company = candidates[-1]
                continue

            if group_company is not None and starts_at_boundary:
                group_company = None

            if group_company is not None:
                title = candidates[-1]
                company = group_company
            else:
                # A date anchors the two lines immediately before it. Earlier
                # lines are location, narrative, skills, or other role-tail
                # text and must not shift title/company parity.
                title = candidates[-2] if len(candidates) >= 2 else candidates[-1]
                company = candidates[-1] if len(candidates) >= 2 else None

            _append_entry(raw_text, output, entry, title, company, dates)
            entry += 1
        return output
    except Exception:
        return []
