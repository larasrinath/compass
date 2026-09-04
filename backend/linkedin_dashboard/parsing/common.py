from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from linkedin_dashboard.parsing.spans import VerifiedSpan
from linkedin_dashboard.parsing.verify import verify_substring

PARSER_VERSION = "m3-v4"


@dataclass(frozen=True, slots=True)
class ParsedValue:
    field_key: str
    value: str
    span: VerifiedSpan
    origin: Literal["deterministic", "llm_verified"] = "deterministic"


@dataclass(frozen=True, slots=True)
class LocatedLine:
    text: str
    start: int


def located_lines(
    raw_text: str, *, headings: set[str] | None = None
) -> list[LocatedLine]:
    ignored = {item.casefold() for item in (headings or set())}
    output: list[LocatedLine] = []
    offset = 0
    for line in raw_text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        left = len(content) - len(content.lstrip())
        text = content.strip()
        if text and text.casefold() not in ignored:
            output.append(LocatedLine(text, offset + left))
        offset += len(line)
    return output


def located_blocks(
    raw_text: str, *, headings: set[str] | None = None
) -> list[list[LocatedLine]]:
    ignored = {item.casefold() for item in (headings or set())}
    blocks: list[list[LocatedLine]] = []
    current: list[LocatedLine] = []
    offset = 0
    for line in raw_text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        left = len(content) - len(content.lstrip())
        text = content.strip()
        if not text:
            if current:
                blocks.append(current)
                current = []
        elif text.casefold() not in ignored:
            current.append(LocatedLine(text, offset + left))
        offset += len(line)
    if current:
        blocks.append(current)
    return blocks


def value(raw_text: str, key: str, line: LocatedLine) -> ParsedValue | None:
    span = verify_substring(raw_text, line.text, start_hint=line.start)
    return ParsedValue(key, line.text, span) if span is not None else None


_DATE = re.compile(
    r"(?:\b(?:19|20)\d{2}\b|\bpresent\b|\bcurrent\b|\b\d+\s+(?:mos?|yrs?)\b)",
    re.IGNORECASE,
)


def is_date_line(text: str) -> bool:
    return bool(_DATE.search(text))


def simple_items(
    raw_text: str, section: str, *, details: bool = False
) -> list[ParsedValue]:
    lines = located_lines(raw_text, headings={section, section.replace("_", " ")})
    output: list[ParsedValue] = []
    item = -1
    for line in lines:
        lowered = line.text.casefold()
        if lowered.startswith(("show all", "see all")):
            continue
        if not details or item < 0 or is_date_line(line.text):
            item += 1
            suffix = "name"
        else:
            suffix = "detail"
        parsed = value(raw_text, f"{section}.{item}.{suffix}", line)
        if parsed is not None:
            output.append(parsed)
    return output
