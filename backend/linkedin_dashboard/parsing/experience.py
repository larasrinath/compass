from __future__ import annotations

import re

from linkedin_dashboard.parsing.common import (
    LocatedLine,
    ParsedValue,
    is_date_line,
    located_lines,
    value,
)

_DURATION = re.compile(r"\b\d+\s+(?:mos?|yrs?)\b", re.IGNORECASE)
_DATED_RANGE = re.compile(
    r"(?:\b(?:19|20)\d{2}\b|\bpresent\b|\bcurrent\b)", re.IGNORECASE
)


def _is_group_duration(text: str) -> bool:
    """Identify a parent-employment duration without treating role dates as one."""
    return _DURATION.search(text) is not None and _DATED_RANGE.search(text) is None


def _latest_structural_block(
    raw_text: str, lines: list[LocatedLine]
) -> tuple[list[LocatedLine], bool]:
    """Return lines after the last strong gap, preserving ordinary empty rows."""
    boundary = 0
    for position in range(1, len(lines)):
        previous = lines[position - 1]
        gap = raw_text[previous.start + len(previous.text) : lines[position].start]
        if gap.count("\n") >= 3 or ("\n" not in gap and gap.count("\r") >= 3):
            boundary = position
    return lines[boundary:], boundary > 0


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
        anchors: list[tuple[list[LocatedLine], LocatedLine]] = []
        pending: list[LocatedLine] = []
        for line in lines:
            if is_date_line(line.text):
                anchors.append((pending, line))
                pending = []
            else:
                pending.append(line)

        if not anchors:
            return _parse_without_date_anchors(raw_text, lines)

        output: list[ParsedValue] = []
        entry = 0
        group_company: LocatedLine | None = None
        for candidates, dates in anchors:
            latest_candidates, has_strong_boundary = _latest_structural_block(
                raw_text, candidates
            )
            if not latest_candidates:
                continue

            if _is_group_duration(dates.text) and len(latest_candidates) == 1:
                group_company = latest_candidates[0]
                continue

            if (
                group_company is not None
                and has_strong_boundary
                and len(latest_candidates) >= 2
            ):
                group_company = None

            if group_company is not None:
                title = latest_candidates[-1]
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
