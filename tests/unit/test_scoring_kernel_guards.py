from __future__ import annotations

import ast
import re
from dataclasses import fields, replace
from decimal import Decimal
from hashlib import sha256
from inspect import signature
from pathlib import Path
from typing import cast

import pytest
from linkedin_dashboard.parsing.spans import VerifiedSpan
from linkedin_dashboard.services.brief import PROTECTED_TERMS
from linkedin_dashboard.services.scoring import (
    MATCHER_VERSION,
    MAX_SIGNAL_WEIGHT,
    AbsenceCoverage,
    BriefInput,
    CoverageSet,
    EvidenceSet,
    InvalidCredentialWeightError,
    MissingReason,
    MissingSection,
    MissingSet,
    Polarity,
    ProfileSection,
    ProfileSnapshot,
    Rollup,
    ScoreClaim,
    ScoreSignal,
    ScoreStage,
    ScoringConfigInput,
    SearchContext,
    SectionState,
    SignalId,
    SignalWeight,
    Term,
    Verdict,
    active_signal_ids,
    calculate_score,
    evaluate_signals,
)


def _simple_snapshot() -> ProfileSnapshot:
    return ProfileSnapshot(
        tuple(
            ProfileSection(
                index,
                name,
                SectionState.COMPLETE,
                raw,
                sha256(raw.encode()).hexdigest(),
            )
            for index, (name, raw) in enumerate(
                (
                    ("skills", "Kubernetes"),
                    ("experience", ""),
                    ("main_profile", ""),
                ),
                start=1,
            )
        )
    )


_PACKAGE = (
    Path(__file__).parents[2]
    / "backend"
    / "linkedin_dashboard"
    / "services"
    / "scoring"
)
_PARSING_DEPENDENCIES = (
    _PACKAGE.parents[1] / "parsing" / "spans.py",
    _PACKAGE.parents[1] / "parsing" / "verify.py",
)
_SCORING_IMPORT = "linkedin_dashboard.services.scoring"
_ALLOWED_IMPORTS = frozenset(
    {
        "__future__",
        "dataclasses",
        "decimal",
        "enum",
        "re",
        "typing",
        "unicodedata",
        "linkedin_dashboard.parsing.spans",
        "linkedin_dashboard.parsing.verify",
    }
)


def _is_allowed_import(imported: str) -> bool:
    return (
        imported in _ALLOWED_IMPORTS
        or imported == _SCORING_IMPORT
        or imported.startswith(f"{_SCORING_IMPORT}.")
    )


def _purity_violations(source: str) -> tuple[str, ...]:
    tree = ast.parse(source)
    imports = [
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    ] + [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    ]
    violations = [
        f"import:{imported}" for imported in imports if not _is_allowed_import(imported)
    ]
    calls = [node.func for node in ast.walk(tree) if isinstance(node, ast.Call)]
    violations.extend(
        f"call:{call.id}"
        for call in calls
        if isinstance(call, ast.Name)
        and call.id in {"open", "input", "print", "eval", "exec", "__import__"}
    )
    violations.extend(
        f"call:{call.attr}"
        for call in calls
        if isinstance(call, ast.Attribute)
        and call.attr in {"urandom", "urlopen", "request"}
    )
    return tuple(violations)


def test_kernel_import_graph_is_pure_and_dependencies_are_guarded() -> None:
    for path in (*_PACKAGE.rglob("*.py"), *_PARSING_DEPENDENCIES):
        violations = _purity_violations(path.read_text())
        assert not violations, f"non-pure dependency in {path}: {violations}"


@pytest.mark.parametrize(
    "mutated_import",
    (
        "linkedin_dashboard.services.scoring_persist",
        "linkedin_dashboard.services.scoring_network",
        "linkedin_dashboard.services.scoringevil.types",
    ),
)
def test_kernel_import_guard_rejects_prefix_collision_mutations(
    mutated_import: str,
) -> None:
    source = f"from {mutated_import} import mutate\n"
    assert _purity_violations(source) == (f"import:{mutated_import}",)


def test_kernel_definitions_exclude_sensitive_and_display_only_inputs() -> None:
    sources = "\n".join(path.read_text().casefold() for path in _PACKAGE.rglob("*.py"))
    canonical = re.sub(r"[^a-z0-9]", "", sources)
    assert "profileurn" not in canonical
    assert "messageability" not in canonical
    assert "composeanchor" not in canonical
    for term in PROTECTED_TERMS:
        assert re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", sources) is None


def test_non_scorable_signal_weight_is_rejected() -> None:
    with pytest.raises(ValueError, match="non-scorable"):
        SignalWeight(cast(SignalId, "S-7"), Decimal(1))


def test_search_context_is_typed_but_cannot_enter_calculation() -> None:
    context = SearchContext("search-1", ("F", "S"))
    brief = BriefInput(required_skills=(Term("Kubernetes"),))
    snapshot = _simple_snapshot()
    assert context.relationship_filters == ("F", "S")
    assert "context" not in signature(calculate_score).parameters
    baseline = calculate_score(
        brief,
        ScoringConfigInput(),
        snapshot,
        stage=ScoreStage.PROVISIONAL,
    )
    assert replace(context, relationship_filters=("O",)) != context
    assert (
        calculate_score(
            brief,
            ScoringConfigInput(),
            snapshot,
            stage=ScoreStage.PROVISIONAL,
        )
        == baseline
    )
    snapshot_fields = "".join(
        re.sub(r"[^a-z0-9]", "", item.name.casefold())
        for item in fields(ProfileSnapshot)
    )
    assert "network" not in snapshot_fields
    assert "relationship" not in snapshot_fields
    assert "searchcontext" not in snapshot_fields


def test_empty_primary_with_alias_does_not_activate() -> None:
    brief = BriefInput(required_skills=(Term(" ", ("Kubernetes",)),))
    assert active_signal_ids(brief) == ()
    assert evaluate_signals(brief, ScoringConfigInput(), ProfileSnapshot(())) == ()


def test_positive_credential_weight_with_empty_input_is_rejected_even_if_inert() -> (
    None
):
    config = ScoringConfigInput(
        weights=tuple(
            SignalWeight(
                item,
                Decimal(1) if item is SignalId.CREDENTIAL else Decimal(0),
            )
            for item in SignalId
        )
    )
    with pytest.raises(InvalidCredentialWeightError):
        calculate_score(
            BriefInput(positive_keywords=("systems",)),
            config,
            ProfileSnapshot(()),
            stage=ScoreStage.PROVISIONAL,
        )


def test_claim_provenance_kinds_cannot_be_substituted() -> None:
    missing = MissingSet((MissingSection("skills", MissingReason.NOT_REQUESTED),))
    with pytest.raises(ValueError, match="incompatible"):
        ScoreClaim("skill:x", "x", Verdict.MATCHED, missing)
    with pytest.raises(ValueError, match="incompatible"):
        ScoreClaim("skill:x", "x", Verdict.NOT_MATCHED, missing)
    with pytest.raises(ValueError, match="cannot be empty"):
        CoverageSet(())


def test_absence_coverage_uses_the_kernel_matcher_version() -> None:
    signal = evaluate_signals(
        BriefInput(required_skills=(Term("Rust"),)),
        ScoringConfigInput(),
        _simple_snapshot(),
    )[0]
    provenance = signal.claims[0].provenance
    assert isinstance(provenance, CoverageSet)
    assert {item.matcher_version for item in provenance.entries} == {MATCHER_VERSION}

    coverage = AbsenceCoverage(
        1,
        "skills",
        "sha256",
        ("kubernetes",),
        (),
        MATCHER_VERSION,
    )
    assert coverage.normalized_terms == ("kubernetes",)
    assert coverage.aliases == ()


@pytest.mark.parametrize(
    "normalized_terms",
    (
        (),
        ("",),
        ("Kubernetes",),
        (" kubernetes",),
        ("kubernetes", "kubernetes"),
        ("rust", "kubernetes"),
        cast(tuple[str, ...], (True,)),
    ),
)
def test_absence_coverage_rejects_noncanonical_terms(
    normalized_terms: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError, match="normalized terms"):
        AbsenceCoverage(
            1,
            "skills",
            "sha256",
            normalized_terms,
            (),
            MATCHER_VERSION,
        )


@pytest.mark.parametrize(
    "aliases",
    (
        ("",),
        ("K8s",),
        (" k8s",),
        ("k8s", "k8s"),
        ("rust", "k8s"),
        ("kubernetes",),
        cast(tuple[str, ...], (False,)),
    ),
)
def test_absence_coverage_rejects_noncanonical_aliases(
    aliases: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError, match="aliases"):
        AbsenceCoverage(
            1,
            "skills",
            "sha256",
            ("kubernetes",),
            aliases,
            MATCHER_VERSION,
        )


@pytest.mark.parametrize(
    "matcher_version",
    ("", "scoring-v0", "scoring-v2", cast(str, True), cast(str, None)),
)
def test_absence_coverage_rejects_unknown_matcher_versions(
    matcher_version: str,
) -> None:
    with pytest.raises(ValueError, match="matcher version"):
        AbsenceCoverage(
            1,
            "skills",
            "sha256",
            ("kubernetes",),
            (),
            matcher_version,
        )


def test_contradicted_claim_rejects_mixed_polarity_evidence() -> None:
    signal = evaluate_signals(
        BriefInput(required_skills=(Term("Kubernetes"),)),
        ScoringConfigInput(),
        _simple_snapshot(),
    )[0]
    provenance = signal.claims[0].provenance
    assert isinstance(provenance, EvidenceSet)
    supporting = provenance.entries[0]
    contradicting = replace(supporting, polarity=Polarity.CONTRADICTING)
    with pytest.raises(ValueError, match="exclusively"):
        ScoreClaim(
            "skill:x",
            "x",
            Verdict.CONTRADICTED,
            EvidenceSet((supporting, contradicting)),
        )


@pytest.mark.parametrize(
    "value",
    (
        cast(Decimal, True),
        Decimal("NaN"),
        Decimal("sNaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
        MAX_SIGNAL_WEIGHT + 1,
    ),
)
def test_weight_domain_rejects_nonfinite_bool_and_extreme_values(
    value: Decimal,
) -> None:
    with pytest.raises(ValueError):
        SignalWeight(SignalId.REQUIRED_SKILLS, value)


def test_weight_domain_accepts_bounded_fraction_and_calculates_safely() -> None:
    fractional = SignalWeight(SignalId.REQUIRED_SKILLS, Decimal("0.125"))
    weights = tuple(
        fractional
        if item is SignalId.REQUIRED_SKILLS
        else SignalWeight(
            item,
            Decimal(0) if item is SignalId.CREDENTIAL else MAX_SIGNAL_WEIGHT,
        )
        for item in SignalId
    )
    result = calculate_score(
        BriefInput(required_skills=(Term("Kubernetes"),)),
        ScoringConfigInput(weights),
        _simple_snapshot(),
        stage=ScoreStage.PROVISIONAL,
    )
    assert result.score == Decimal("100.000000")

    maximum = ScoringConfigInput(
        tuple(
            SignalWeight(
                item,
                MAX_SIGNAL_WEIGHT if item is SignalId.REQUIRED_SKILLS else Decimal(0),
            )
            for item in SignalId
        )
    )
    assert calculate_score(
        BriefInput(required_skills=(Term("Kubernetes"),)),
        maximum,
        _simple_snapshot(),
        stage=ScoreStage.PROVISIONAL,
    ).score == Decimal("100.000000")


@pytest.mark.parametrize(
    "value",
    (cast(int, True), cast(int, 1.5), cast(int, Decimal("1.5"))),
)
def test_required_months_reject_bool_and_fractional_values(value: int) -> None:
    with pytest.raises(ValueError, match="integer"):
        BriefInput(required_experience_months=value)


def test_signal_rollup_cannot_disagree_with_claims() -> None:
    claim = ScoreClaim(
        "skill:x",
        "x",
        Verdict.UNKNOWN,
        MissingSet((MissingSection("skills", MissingReason.NOT_REQUESTED),)),
    )
    with pytest.raises(ValueError, match="rollup"):
        ScoreSignal(
            SignalId.REQUIRED_SKILLS,
            Rollup.MATCHED,
            Decimal(0),
            Decimal(0),
            (claim,),
        )


def test_evidence_span_cannot_be_constructed_without_verification() -> None:
    with pytest.raises(TypeError, match="exact verification"):
        VerifiedSpan(0, 1, "x")
