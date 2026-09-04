from __future__ import annotations

import random
from dataclasses import replace
from decimal import Decimal

import pytest
from linkedin_dashboard.services.scoring import (
    BriefInput,
    CalculationStatus,
    ConfidenceBand,
    MissingReason,
    MissingSection,
    MissingSet,
    PenaltyContribution,
    Rollup,
    ScoreClaim,
    ScoreSignal,
    ScoreStage,
    ScoringConfigInput,
    SignalId,
    SignalWeight,
    Verdict,
    ZeroEffectiveWeightError,
    aggregate,
    calculate_score,
)

from tests.scoring_fixtures import full_brief, rich_snapshot


def _claim(key: str = "test") -> ScoreClaim:
    return ScoreClaim(
        key,
        key,
        Verdict.UNKNOWN,
        MissingSet((MissingSection("experience", MissingReason.FETCH_ERROR),)),
    )


def _signal(
    signal_id: SignalId, score: Decimal | str | int, availability: Decimal | str | int
) -> ScoreSignal:
    return ScoreSignal(
        signal_id,
        Rollup.UNKNOWN,
        Decimal(str(score)),
        Decimal(str(availability)),
        (_claim(signal_id.value),),
    )


def _weights(**overrides: Decimal | str | int) -> ScoringConfigInput:
    values = {item.value: Decimal(1) for item in SignalId}
    values.update(
        {key.replace("_", "-"): Decimal(str(value)) for key, value in overrides.items()}
    )
    return ScoringConfigInput(
        weights=tuple(
            SignalWeight(signal_id, values[signal_id.value]) for signal_id in SignalId
        )
    )


def test_fractional_availability_counterexample() -> None:
    signals = (
        _signal(SignalId.REQUIRED_SKILLS, 1, "0.5"),
        _signal(SignalId.OPTIONAL_SKILLS, 0, 1),
    )
    result = aggregate(
        active=(SignalId.REQUIRED_SKILLS, SignalId.OPTIONAL_SKILLS),
        signals=signals,
        config=_weights(),
        stage=ScoreStage.PROVISIONAL,
    )

    assert result.score_lower == Decimal("25.000000")
    assert result.score == Decimal("33.333333")
    assert result.score_upper == Decimal("50.000000")
    assert result.confidence == Decimal("0.750000")
    assert result.confidence_band is ConfidenceBand.MEDIUM


def test_random_fractional_bounds_and_clamps() -> None:
    generator = random.Random(9402)
    ids = tuple(SignalId)
    for _ in range(500):
        count = generator.randint(1, len(ids))
        active = ids[:count]
        weights = {item.value: Decimal(str(generator.randint(0, 50))) for item in ids}
        if not any(weights[item.value] > 0 for item in active):
            weights[active[0].value] = Decimal(1)
        config = ScoringConfigInput(
            weights=tuple(SignalWeight(item, weights[item.value]) for item in ids)
        )
        signals = tuple(
            _signal(
                item,
                Decimal(generator.randint(0, 1000)) / 1000,
                Decimal(generator.randint(0, 1000)) / 1000,
            )
            for item in active
        )
        penalty = PenaltyContribution(
            "P-1", Decimal(generator.randint(0, 15)), ("generated",)
        )
        result = aggregate(
            active=active,
            signals=signals,
            config=config,
            stage=ScoreStage.ENRICHED,
            penalties=(penalty,),
        )
        if result.score is not None:
            assert result.score_lower is not None
            assert result.score_upper is not None
            assert (
                Decimal(0)
                <= result.score_lower
                <= result.score
                <= result.score_upper
                <= Decimal(100)
            )
            assert Decimal(0) <= result.confidence <= Decimal(1)


def test_penalty_is_identical_for_score_and_bounds_before_clamp() -> None:
    signal = _signal(SignalId.REQUIRED_SKILLS, "0.1", 1)
    result = aggregate(
        active=(SignalId.REQUIRED_SKILLS,),
        signals=(signal,),
        config=_weights(),
        stage=ScoreStage.PROVISIONAL,
        penalties=(PenaltyContribution("P-1", Decimal(15), ("x",)),),
    )
    assert (
        result.score == result.score_lower == result.score_upper == Decimal("0.000000")
    )


def test_named_penalty_cannot_bypass_its_cap_with_duplicate_rows() -> None:
    signal = _signal(SignalId.REQUIRED_SKILLS, 1, 1)
    duplicate = PenaltyContribution("P-1", Decimal(15), ("x",))
    with pytest.raises(ValueError, match="only once"):
        aggregate(
            active=(SignalId.REQUIRED_SKILLS,),
            signals=(signal,),
            config=_weights(),
            stage=ScoreStage.PROVISIONAL,
            penalties=(duplicate, duplicate),
        )


def test_all_unknown_is_nullable_and_distinct_from_all_inert() -> None:
    unknown = aggregate(
        active=(SignalId.REQUIRED_SKILLS,),
        signals=(_signal(SignalId.REQUIRED_SKILLS, 0, 0),),
        config=_weights(),
        stage=ScoreStage.PROVISIONAL,
    )
    inert = aggregate(
        active=(),
        signals=(),
        config=_weights(),
        stage=ScoreStage.PROVISIONAL,
    )

    assert unknown.score is unknown.score_lower is unknown.score_upper is None
    assert unknown.confidence == 0
    assert unknown.confidence_band is None
    assert unknown.active_signal_count == 1
    assert unknown.calculation_status is CalculationStatus.UNKNOWN
    assert inert.score is inert.score_lower is inert.score_upper is None
    assert inert.confidence == 0
    assert inert.confidence_band is ConfidenceBand.LOW
    assert inert.active_signal_count == 0
    assert inert.signals == inert.penalties == ()


def test_positive_keyword_only_brief_is_valid_all_inert_no_score() -> None:
    result = calculate_score(
        BriefInput(positive_keywords=("distributed systems",)),
        ScoringConfigInput(),
        rich_snapshot(),
        stage=ScoreStage.PROVISIONAL,
    )
    assert result == replace(
        result,
        score=None,
        score_lower=None,
        score_upper=None,
        confidence=Decimal(0),
        confidence_band=ConfidenceBand.LOW,
        calculation_status=CalculationStatus.UNKNOWN,
        active_signal_count=0,
        signals=(),
        penalties=(),
    )


def test_active_zero_weight_rejected_before_division() -> None:
    config = ScoringConfigInput(
        weights=tuple(SignalWeight(item, Decimal(0)) for item in SignalId)
    )
    with pytest.raises(ZeroEffectiveWeightError):
        aggregate(
            active=(SignalId.REQUIRED_SKILLS,),
            signals=(_signal(SignalId.REQUIRED_SKILLS, 1, 1),),
            config=config,
            stage=ScoreStage.PROVISIONAL,
        )


def test_calculation_is_identical_across_100_runs_and_shuffled_sections() -> None:
    brief = full_brief()
    snapshot = rich_snapshot()
    expected = calculate_score(
        brief,
        ScoringConfigInput(),
        snapshot,
        stage=ScoreStage.ENRICHED,
    )
    assert all(
        calculate_score(
            brief,
            ScoringConfigInput(),
            snapshot,
            stage=ScoreStage.ENRICHED,
        )
        == expected
        for _ in range(100)
    )
    shuffled = replace(snapshot, sections=tuple(reversed(snapshot.sections)))
    assert (
        calculate_score(
            brief,
            ScoringConfigInput(),
            shuffled,
            stage=ScoreStage.ENRICHED,
        )
        == expected
    )


def test_negative_keywords_are_penalties_not_activation() -> None:
    inert = calculate_score(
        BriefInput(negative_keywords=("kubernetes",)),
        ScoringConfigInput(),
        rich_snapshot(),
        stage=ScoreStage.PROVISIONAL,
    )
    active = calculate_score(
        BriefInput(
            required_skills=full_brief().required_skills,
            negative_keywords=("kubernetes", "banking"),
        ),
        ScoringConfigInput(),
        rich_snapshot(),
        stage=ScoreStage.PROVISIONAL,
    )
    assert inert.penalties == ()
    assert active.penalties[0].penalty_id == "P-1"
    assert active.penalties[0].points == 6
    assert all(item.span.snippet for item in active.penalties[0].evidence)


def test_contradiction_penalty_is_named_and_bounded() -> None:
    result = calculate_score(
        full_brief(),
        ScoringConfigInput(),
        rich_snapshot(months=12),
        stage=ScoreStage.ENRICHED,
    )
    contradiction = next(item for item in result.penalties if item.penalty_id == "P-2")
    assert contradiction.points == 5
    assert contradiction.details == ("S-3:experience-depth",)
    assert contradiction.evidence
