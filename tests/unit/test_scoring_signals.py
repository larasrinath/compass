from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest
from linkedin_dashboard.services.scoring import (
    CoverageSet,
    EvidenceSet,
    Matcher,
    MetroEquivalence,
    MissingSet,
    Rollup,
    ScoringConfigInput,
    SignalId,
    Term,
    Verdict,
    active_signal_ids,
    evaluate_signals,
    find_term_matches,
)
from linkedin_dashboard.services.scoring.signals.experience import relevant_experience
from linkedin_dashboard.services.scoring.signals.location import location_fit
from linkedin_dashboard.services.scoring.signals.skills import required_skills

from tests.scoring_fixtures import (
    full_brief,
    missing_section,
    replace_section,
    rich_snapshot,
)


def test_unicode_whole_token_alias_stem_and_repeated_spans() -> None:
    raw = "🛰️ cafe\u0301 k8s developed Kubernetes Kuberneteses Kubernetes"
    cafe = find_term_matches(raw, Term("CAFÉ"))
    alias = find_term_matches(raw, Term("Kubernetes", ("k8s",)))
    stem = find_term_matches(raw, Term("develop"))

    assert len(cafe) == 1
    assert raw[cafe[0].span.start : cafe[0].span.end] == "cafe\u0301"
    assert [(item.matched_term, item.matcher) for item in alias] == [
        ("k8s", Matcher.ALIAS),
        ("Kubernetes", Matcher.EXACT),
        ("Kubernetes", Matcher.EXACT),
    ]
    assert [item.span.start for item in alias] == sorted(
        item.span.start for item in alias
    )
    assert stem[0].matched_term == "developed"
    assert stem[0].matcher is Matcher.STEM

    repeated = find_term_matches("🛰️ Kubernetes 🛰️ Kubernetes", Term("Kubernetes"))
    assert [item.span.start for item in repeated] == [3, 17]
    assert not find_term_matches("C++builder", Term("C++"))
    assert find_term_matches("C++ builder", Term("C++"))[0].matched_term == "C++"


def test_term_signal_can_roll_up_mixed_claims() -> None:
    snapshot = replace_section(rich_snapshot(), "skills", missing_section("skills", 3))
    signal = required_skills(
        (Term("Kubernetes"), Term("Rust")),
        snapshot,
    )

    assert signal.rollup is Rollup.MIXED
    assert [item.verdict for item in signal.claims] == [
        Verdict.MATCHED,
        Verdict.UNKNOWN,
    ]
    assert isinstance(signal.claims[0].provenance, EvidenceSet)
    assert isinstance(signal.claims[1].provenance, MissingSet)
    assert signal.raw_subscore == Decimal("0.5")
    assert signal.availability == Decimal("0.5")


def test_term_signal_not_matched_has_complete_coverage_and_no_span() -> None:
    signal = required_skills((Term("Rust", ("rustlang",)),), rich_snapshot())

    assert signal.rollup is Rollup.NOT_MATCHED
    assert signal.raw_subscore == 0
    provenance = signal.claims[0].provenance
    assert isinstance(provenance, CoverageSet)
    assert [item.section_name for item in provenance.entries] == [
        "experience",
        "main_profile",
        "skills",
    ]
    assert all(item.normalized_terms == ("rust",) for item in provenance.entries)
    assert all(item.aliases == ("rustlang",) for item in provenance.entries)
    assert all(not hasattr(item, "span") for item in provenance.entries)


@pytest.mark.parametrize(
    ("months", "verdict", "subscore", "availability"),
    [
        (72, Verdict.MATCHED, Decimal(1), Decimal(1)),
        (12, Verdict.CONTRADICTED, Decimal("0.2"), Decimal(1)),
        (None, Verdict.UNKNOWN, Decimal(0), Decimal("0.5")),
    ],
)
def test_experience_verdicts(
    months: int | None,
    verdict: Verdict,
    subscore: Decimal,
    availability: Decimal,
) -> None:
    signal = relevant_experience(full_brief(), rich_snapshot(months=months))
    assert signal.claims[0].verdict is verdict
    assert signal.raw_subscore == subscore
    assert signal.availability == availability


def test_experience_not_matched_without_relevant_roles() -> None:
    brief = replace(
        full_brief(),
        required_skills=(Term("Rust"),),
        target_titles=(Term("Designer"),),
    )
    signal = relevant_experience(brief, rich_snapshot())
    assert signal.claims[0].verdict is Verdict.NOT_MATCHED
    assert isinstance(signal.claims[0].provenance, CoverageSet)


def test_scalar_title_and_industry_signals_have_one_claim() -> None:
    signals = {
        item.signal_id: item
        for item in evaluate_signals(
            full_brief(), ScoringConfigInput(), rich_snapshot()
        )
    }

    assert signals[SignalId.TITLE].raw_subscore == Decimal(1)
    assert signals[SignalId.TITLE].claims[0].verdict is Verdict.MATCHED
    assert signals[SignalId.INDUSTRY].raw_subscore == 1
    assert signals[SignalId.INDUSTRY].claims[0].verdict is Verdict.MATCHED
    assert len(signals[SignalId.TITLE].claims) == 1
    assert len(signals[SignalId.INDUSTRY].claims) == 1

    snapshot = rich_snapshot()
    main_title_only = replace(
        snapshot,
        titles=tuple(
            item for item in snapshot.titles if item.section_name == "main_profile"
        ),
    )
    partial = {
        item.signal_id: item
        for item in evaluate_signals(
            full_brief(), ScoringConfigInput(), main_title_only
        )
    }
    assert partial[SignalId.TITLE].raw_subscore == Decimal("0.8")


def test_scalar_unknown_and_not_matched_paths() -> None:
    snapshot = rich_snapshot()
    missing = replace_section(
        replace_section(snapshot, "experience", missing_section("experience", 2)),
        "main_profile",
        missing_section("main_profile", 1),
    )
    unknown = {
        item.signal_id: item
        for item in evaluate_signals(full_brief(), ScoringConfigInput(), missing)
    }
    assert unknown[SignalId.TITLE].claims[0].verdict is Verdict.UNKNOWN
    assert unknown[SignalId.INDUSTRY].claims[0].verdict is Verdict.UNKNOWN

    brief = replace(
        full_brief(),
        target_titles=(Term("Designer"),),
        industries=(Term("Aerospace"),),
    )
    absent = {
        item.signal_id: item
        for item in evaluate_signals(brief, ScoringConfigInput(), snapshot)
    }
    assert absent[SignalId.TITLE].claims[0].verdict is Verdict.NOT_MATCHED
    assert absent[SignalId.INDUSTRY].claims[0].verdict is Verdict.NOT_MATCHED


def test_location_exact_metro_absent_unparsed_and_missing() -> None:
    brief = full_brief()
    snapshot = rich_snapshot()
    assert location_fit(brief, ScoringConfigInput(), snapshot).raw_subscore == 1

    metro_brief = replace(brief, location="Evanston")
    metro_config = ScoringConfigInput(
        metro_equivalences=(MetroEquivalence("Chicagoland", ("Chicago", "Evanston")),)
    )
    metro = location_fit(metro_brief, metro_config, snapshot)
    assert metro.raw_subscore == Decimal("0.6")
    assert metro.claims[0].verdict is Verdict.MATCHED
    assert (
        location_fit(metro_brief, ScoringConfigInput(), snapshot).claims[0].verdict
        is Verdict.NOT_MATCHED
    )

    unparsed = replace(snapshot, location=None)
    assert (
        location_fit(brief, ScoringConfigInput(), unparsed).claims[0].verdict
        is Verdict.UNKNOWN
    )
    missing = replace_section(
        snapshot, "main_profile", missing_section("main_profile", 1)
    )
    assert location_fit(brief, ScoringConfigInput(), missing).availability == 0


def test_credential_exact_alias_and_unknown() -> None:
    exact = {
        item.signal_id: item
        for item in evaluate_signals(
            full_brief(), ScoringConfigInput(), rich_snapshot()
        )
    }[SignalId.CREDENTIAL]
    assert exact.claims[0].verdict is Verdict.MATCHED
    exact_provenance = exact.claims[0].provenance
    assert isinstance(exact_provenance, EvidenceSet)
    assert exact_provenance.entries[0].matcher is Matcher.EXACT

    alias_brief = replace(
        full_brief(), required_credentials=(Term("Solutions Architect", ("AWS",)),)
    )
    alias = {
        item.signal_id: item
        for item in evaluate_signals(alias_brief, ScoringConfigInput(), rich_snapshot())
    }[SignalId.CREDENTIAL]
    alias_provenance = alias.claims[0].provenance
    assert isinstance(alias_provenance, EvidenceSet)
    assert alias_provenance.entries[0].matcher is Matcher.ALIAS

    missing = replace_section(
        replace_section(rich_snapshot(), "education", missing_section("education", 4)),
        "certifications",
        missing_section("certifications", 5),
    )
    unknown = {
        item.signal_id: item
        for item in evaluate_signals(full_brief(), ScoringConfigInput(), missing)
    }[SignalId.CREDENTIAL]
    assert unknown.claims[0].verdict is Verdict.UNKNOWN


@pytest.mark.parametrize(
    ("field", "empty", "removed"),
    [
        ("required_skills", (), SignalId.REQUIRED_SKILLS),
        ("optional_skills", (), SignalId.OPTIONAL_SKILLS),
        ("required_experience_months", None, SignalId.EXPERIENCE),
        ("required_experience_months", 0, SignalId.EXPERIENCE),
        ("target_titles", (), SignalId.TITLE),
        ("industries", (), SignalId.INDUSTRY),
        ("location", "", SignalId.LOCATION),
        ("location", "   ", SignalId.LOCATION),
        ("required_credentials", (), SignalId.CREDENTIAL),
    ],
)
def test_input_activity_matrix_removes_only_one_signal(
    field: str, empty: object, removed: SignalId
) -> None:
    brief = replace(full_brief(), **{field: empty})
    active = active_signal_ids(brief)
    assert removed not in active
    assert set(active) == set(SignalId) - {removed}
    emitted = evaluate_signals(brief, ScoringConfigInput(), rich_snapshot())
    assert tuple(item.signal_id for item in emitted) == active
    assert all(item.claims for item in emitted)


def test_shuffled_inputs_produce_stable_signal_order() -> None:
    brief = replace(
        full_brief(),
        required_skills=(
            Term("Kubernetes", ("k8s",)),
            Term("Python", ("py",)),
            Term("kubernetes", ("kube",)),
        ),
    )
    shuffled = replace(
        brief,
        required_skills=tuple(reversed(brief.required_skills)),
        optional_skills=tuple(reversed(brief.optional_skills)),
        industries=tuple(reversed(brief.industries)),
    )
    assert evaluate_signals(
        brief, ScoringConfigInput(), rich_snapshot()
    ) == evaluate_signals(shuffled, ScoringConfigInput(), rich_snapshot())
