from __future__ import annotations

from linkedin_dashboard.parsing.common import ParsedValue, located_lines, value


def parse(raw_text: str) -> list[ParsedValue]:
    try:
        lines = located_lines(raw_text, headings={"main profile", "profile"})
        keys = ("name", "headline", "location")
        output = [
            parsed
            for key, line in zip(keys, lines, strict=False)
            if (parsed := value(raw_text, key, line)) is not None
        ]
        for index, line in enumerate(lines[3:]):
            parsed = value(raw_text, f"about.{index}", line)
            if parsed is not None:
                output.append(parsed)
        return output
    except Exception:
        return []
