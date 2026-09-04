from linkedin_dashboard.parsing import parse_section


def test_main_profile_skips_header_metadata_and_stops_about_before_activity():
    raw = (
        "Ada Example\n\nShe/Her\n\n· 1st\n\nPrincipal Planning Advisor\n\n"
        "Washington DC-Baltimore Area\n\n·\n\nContact info\n\nExample Company\n\n"
        "Highlights\n\nAbout\n\nI design workforce planning models.\n\n"
        "Top skills\n\nPlanning\n\nActivity\n\nReposted content about another person."
    )
    fields = parse_section("main_profile", raw)
    values = {field.field_key: field.value for field in fields}
    assert values == {
        "name": "Ada Example",
        "headline": "Principal Planning Advisor",
        "location": "Washington DC-Baltimore Area",
        "about.0": "I design workforce planning models.",
    }
    for field in fields:
        assert raw[field.span.start : field.span.end] == field.value


def test_grouped_employer_with_employment_type_and_ordinary_spacing():
    raw = (
        "Experience\n\nExample Planning\n\nFull-time · 6 yrs 6 mos\n\n"
        "Washington DC-Baltimore Area\n\nPrincipal Applications Advisor\n\n"
        "Jul 2026 - Present · 3 mos\n\nRemote\n\nSenior Delivery Lead\n\n"
        "May 2025 - Jul 2026 · 1 yr 3 mos\n\nRemote\n\nSkills: Planning models\n\n"
        "Senior Application Architect\n\nMar 2024 - Jul 2025 · 1 yr 5 mos\n\n"
        "Skills: Planning models\n\nDirector Client Success\n\nExample Consulting\n\n"
        "Oct 2018 - Apr 2020 · 1 yr 7 mos"
    )
    fields = parse_section("experience", raw)
    values = {field.field_key: field.value for field in fields}
    assert [values[f"experience.{i}.title"] for i in range(4)] == [
        "Principal Applications Advisor",
        "Senior Delivery Lead",
        "Senior Application Architect",
        "Director Client Success",
    ]
    assert [values[f"experience.{i}.company"] for i in range(4)] == [
        "Example Planning",
        "Example Planning",
        "Example Planning",
        "Example Consulting",
    ]
    for field in fields:
        assert raw[field.span.start : field.span.end] == field.value
