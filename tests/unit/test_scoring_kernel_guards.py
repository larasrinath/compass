from __future__ import annotations

import ast
import re
from dataclasses import replace
from decimal import Decimal
from inspect import signature
from pathlib import Path
from typing import cast

import pytest
from linkedin_dashboard.parsing.spans import VerifiedSpan
from linkedin_dashboard.services.brief import PROTECTED_TERMS
from linkedin_dashboard.services.scoring import (
    BriefInput,
    CoverageSet,
    InvalidCredentialWeightError,
    MissingReason,
    MissingSection,
    MissingSet,
    Rollup,
    ScoreClaim,
    ScoreSignal,
    ScoreStage,
    ScoringConfigInput,
    SearchContext,
    SignalId,
    SignalWeight,
    Term,
    Verdict,
    active_signal_ids,
    calculate_score,
    evaluate_signals,
)

from tests.scoring_fixtures import full_brief, rich_snapshot

_PACKAGE = (
    Path(__file__).parents[2]
    / "backend"
    / "linkedin_dashboard"
    / "services"
    / "scoring"
)
_FORBIDDEN_IMPORTS = (
    "datetime",
    "fastapi",
    "httpx",
    "random",
    "requests",
    "socket",
    "sqlalchemy",
    "time",
    "linkedin_dashboard.api",
    "linkedin_dashboard.db",
    "linkedin_dashboard.mcp",
)


def test_kernel_import_graph_is_pure() -> None:
    for path in _PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text())
        imports = [
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        ] + [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        ]
        assert not any(
            imported == blocked or imported.startswith(f"{blocked}.")
            for imported in imports
            for blocked in _FORBIDDEN_IMPORTS
        ), f"forbidden import in {path}"


def test_kernel_definitions_exclude_sensitive_and_display_only_inputs() -> None:
    sources = "\n".join(path.read_text().casefold() for path in _PACKAGE.rglob("*.py"))
    assert "profile_urn" not in sources
    assert "messageability" not in sources
    assert "compose-anchor" not in sources
    for term in PROTECTED_TERMS:
        assert re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", sources) is None


def test_non_scorable_signal_weight_is_rejected() -> None:
    with pytest.raises(ValueError, match="non-scorable"):
        SignalWeight(cast(SignalId, "S-7"), Decimal(1))


def test_search_context_is_typed_but_cannot_enter_calculation() -> None:
    context = SearchContext("search-1", ("F", "S"))
    assert context.relationship_filters == ("F", "S")
    assert "context" not in signature(calculate_score).parameters
    baseline = calculate_score(
        full_brief(),
        ScoringConfigInput(),
        rich_snapshot(),
        stage=ScoreStage.PROVISIONAL,
    )
    assert replace(context, relationship_filters=("O",)) != context
    assert (
        calculate_score(
            full_brief(),
            ScoringConfigInput(),
            rich_snapshot(),
            stage=ScoreStage.PROVISIONAL,
        )
        == baseline
    )


def test_empty_primary_with_alias_does_not_activate() -> None:
    brief = BriefInput(required_skills=(Term(" ", ("Kubernetes",)),))
    assert active_signal_ids(brief) == ()
    assert evaluate_signals(brief, ScoringConfigInput(), rich_snapshot()) == ()


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
            rich_snapshot(),
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
