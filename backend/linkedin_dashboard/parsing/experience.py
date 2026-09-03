from __future__ import annotations

from linkedin_dashboard.parsing.common import (
    ParsedValue,
    is_date_line,
    located_lines,
    value,
)


def parse(raw_text: str) -> list[ParsedValue]:
    """Parse conservative title/company pairs without inventing missing data."""
    try:
        lines = located_lines(raw_text, headings={"experience"})
        output: list[ParsedValue] = []
        entry = 0
        position = 0
        while position < len(lines):
            title = lines[position]
            if is_date_line(title.text):
                position += 1
                continue
            parsed = value(raw_text, f"experience.{entry}.title", title)
            if parsed is not None:
                output.append(parsed)
            position += 1
            if position < len(lines) and not is_date_line(lines[position].text):
                parsed = value(raw_text, f"experience.{entry}.company", lines[position])
                if parsed is not None:
                    output.append(parsed)
                position += 1
            while position < len(lines) and is_date_line(lines[position].text):
                parsed = value(raw_text, f"experience.{entry}.dates", lines[position])
                if parsed is not None:
                    output.append(parsed)
                position += 1
            entry += 1
        return output
    except Exception:
        return []
