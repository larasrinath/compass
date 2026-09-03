from linkedin_dashboard.parsing.common import ParsedValue, simple_items


def parse(raw_text: str) -> list[ParsedValue]:
    try:
        return simple_items(raw_text, "skills")
    except Exception:
        return []
