"""Evidence-preserving profile parsers. Implemented in M3."""

from __future__ import annotations

from collections.abc import Callable

from linkedin_dashboard.parsing import (
    certifications,
    education,
    experience,
    main_profile,
    projects,
    skills,
)
from linkedin_dashboard.parsing.common import ParsedValue

SectionParser = Callable[[str], list[ParsedValue]]

PARSERS: dict[str, SectionParser] = {
    "main_profile": main_profile.parse,
    "experience": experience.parse,
    "skills": skills.parse,
    "education": education.parse,
    "projects": projects.parse,
    "certifications": certifications.parse,
}


def parse_section(section_name: str, raw_text: str) -> list[ParsedValue]:
    parser = PARSERS.get(section_name)
    return parser(raw_text) if parser is not None else []
