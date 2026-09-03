from linkedin_dashboard.parsing.common import (
    ParsedValue,
    is_date_line,
    located_blocks,
    value,
)


def parse(raw_text: str) -> list[ParsedValue]:
    try:
        output: list[ParsedValue] = []
        for index, block in enumerate(located_blocks(raw_text, headings={"education"})):
            for position, line in enumerate(block):
                key = (
                    "school"
                    if position == 0
                    else "dates"
                    if is_date_line(line.text)
                    else "degree"
                    if position == 1
                    else "detail"
                )
                parsed = value(raw_text, f"education.{index}.{key}", line)
                if parsed is not None:
                    output.append(parsed)
        return output
    except Exception:
        return []
