from __future__ import annotations

from dataclasses import replace
from hashlib import sha256

from linkedin_dashboard.parsing.verify import verify_substring
from linkedin_dashboard.services.scoring import (
    BriefInput,
    ExperienceRole,
    MissingReason,
    ProfileSection,
    ProfileSnapshot,
    SectionState,
    SourcedText,
    Term,
)


def complete_section(name: str, raw_text: str, section_id: int) -> ProfileSection:
    return ProfileSection(
        section_id=section_id,
        name=name,
        state=SectionState.COMPLETE,
        raw_text=raw_text,
        content_sha256=sha256(raw_text.encode()).hexdigest(),
    )


def missing_section(
    name: str, section_id: int, reason: MissingReason = MissingReason.FETCH_ERROR
) -> ProfileSection:
    return ProfileSection(
        section_id=section_id,
        name=name,
        state=SectionState.MISSING,
        missing_reason=reason,
    )


def sourced(section: ProfileSection, text: str, occurrence: int = 0) -> SourcedText:
    cursor = -1
    for _ in range(occurrence + 1):
        cursor = section.raw_text.find(text, cursor + 1)
    assert cursor >= 0
    span = verify_substring(section.raw_text, text, start_hint=cursor)
    assert span is not None
    return SourcedText(
        section_name=section.name,
        section_id=section.section_id,
        content_sha256=section.content_sha256,
        text=text,
        span=span,
    )


def rich_snapshot(*, months: int | None = 72) -> ProfileSnapshot:
    main = complete_section(
        "main_profile",
        "Ada Example\nStaff Backend Engineer\nChicago\nFinancial services leader",
        1,
    )
    experience = complete_section(
        "experience",
        "Backend Engineer\nBuilt Kubernetes platforms for banking.\n",
        2,
    )
    skills = complete_section("skills", "Python\nKubernetes\n", 3)
    education = complete_section("education", "State University\n", 4)
    certifications = complete_section(
        "certifications", "AWS Certified Solutions Architect\n", 5
    )
    title = sourced(experience, "Backend Engineer")
    role = ExperienceRole(
        title=title,
        description=sourced(experience, "Built Kubernetes platforms for banking."),
        months=months,
    )
    return ProfileSnapshot(
        sections=(certifications, skills, main, education, experience),
        titles=(sourced(main, "Staff Backend Engineer"), title),
        location=sourced(main, "Chicago"),
        experience_roles=(role,),
    )


def full_brief() -> BriefInput:
    return BriefInput(
        required_skills=(Term("Kubernetes", ("k8s",)),),
        optional_skills=(Term("Python"),),
        required_experience_months=60,
        target_titles=(Term("Backend Engineer"),),
        industries=(Term("financial services", ("banking",)),),
        location="Chicago",
        required_credentials=(Term("AWS Certified Solutions Architect", ("AWS SAA",)),),
        positive_keywords=("distributed systems",),
        negative_keywords=("gambling",),
    )


def replace_section(
    snapshot: ProfileSnapshot, name: str, replacement: ProfileSection
) -> ProfileSnapshot:
    return replace(
        snapshot,
        sections=tuple(
            replacement if section.name == name else section
            for section in snapshot.sections
        ),
        titles=tuple(item for item in snapshot.titles if item.section_name != name),
        location=(
            None
            if snapshot.location is not None and snapshot.location.section_name == name
            else snapshot.location
        ),
        experience_roles=(() if name == "experience" else snapshot.experience_roles),
    )
