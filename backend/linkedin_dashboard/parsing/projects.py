from linkedin_dashboard.parsing.common import (
    ParsedValue,
    is_date_line,
    located_blocks,
    value,
)


def parse(raw_text: str) -> list[ParsedValue]:
    try:
        output: list[ParsedValue] = []
        for index, block in enumerate(located_blocks(raw_text, headings={"projects"})):
            for position, line in enumerate(block):
                key = (
                    "name"
                    if position == 0
                    else "dates"
                    if is_date_line(line.text)
                    else "description"
                )
                parsed = value(raw_text, f"projects.{index}.{key}", line)
                if parsed is not None:
                    output.append(parsed)
        return output
    except Exception:
        return []
