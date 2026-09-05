from __future__ import annotations

import re

from linkedin_dashboard.parsing.common import ParsedValue, located_lines, value

_HEADER_NOISE = re.compile(
    r"(?:[·•]\s*)?(?:1st|2nd|3rd\+?|\d[rdnths]+)"
    r"|(?:he|him|his|she|her|hers|they|them|their|theirs|any)"
    r"(?:\s*/\s*(?:he|him|his|she|her|hers|they|them|their|theirs|any))+"
    r"|[·•]",
    re.IGNORECASE,
)
_BOUNDARIES = {
    "contact info",
    "message",
    "connect",
    "follow",
    "more",
    "highlights",
    "about",
    "top skills",
    "activity",
    "experience",
    "education",
    "skills",
    "recommendations",
    "interests",
    "licenses & certifications",
    "projects",
}


def parse(raw_text: str) -> list[ParsedValue]:
    """Keep header metadata and activity-feed text out of professional fields."""
    try:
        lines = located_lines(raw_text, headings={"main profile", "profile"})
        header = []
        for line in lines:
            if line.text.casefold() in _BOUNDARIES:
                break
            if not _HEADER_NOISE.fullmatch(line.text):
                header.append(line)
            if len(header) == 3:
                break
        output = [
            parsed
            for key, line in zip(("name", "headline", "location"), header, strict=False)
            if (parsed := value(raw_text, key, line)) is not None
        ]
        about_index = next(
            (
                index
                for index, line in enumerate(lines)
                if line.text.casefold() == "about"
            ),
            None,
        )
        if about_index is not None:
            about_lines = []
            for line in lines[about_index + 1 :]:
                if line.text.casefold() in _BOUNDARIES:
                    break
                if line.text.casefold() not in {"… more", "... more", "see more"}:
                    about_lines.append(line)
        else:
            # Preserve simple, already-sectioned fixtures; never relabel page
            # navigation, highlights, or an activity feed as the person's About.
            tail = [line for line in lines if header and line.start > header[-1].start]
            about_lines = []
            for line in tail:
                if line.text.casefold() in _BOUNDARIES:
                    break
                about_lines.append(line)
        for index, line in enumerate(about_lines):
            parsed = value(raw_text, f"about.{index}", line)
            if parsed is not None:
                output.append(parsed)
        return output
    except Exception:
        return []
