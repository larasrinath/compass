from linkedin_dashboard.parsing.common import ParsedValue, located_blocks, value


def parse(raw_text: str) -> list[ParsedValue]:
    try:
        output: list[ParsedValue] = []
        for index, block in enumerate(
            located_blocks(raw_text, headings={"certifications"})
        ):
            for position, line in enumerate(block):
                lowered = line.text.casefold()
                key = (
                    "name"
                    if position == 0
                    else "expires"
                    if lowered.startswith("expires")
                    else "issued"
                    if lowered.startswith("issued")
                    else "issuer"
                    if position == 1
                    else "detail"
                )
                parsed = value(raw_text, f"certifications.{index}.{key}", line)
                if parsed is not None:
                    output.append(parsed)
        return output
    except Exception:
        return []
