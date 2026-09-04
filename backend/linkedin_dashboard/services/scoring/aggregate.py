"""Pure deterministic aggregation, bounds, confidence and penalties."""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal, localcontext

from linkedin_dashboard.services.scoring.matching import (
    find_term_matches,
    normalize_text,
)
from linkedin_dashboard.services.scoring.signals import (
    InvalidCredentialWeightError,
    active_signal_ids,
    evaluate_signals,
)
from linkedin_dashboard.services.scoring.signals.common import evidence_for_match
from linkedin_dashboard.services.scoring.types import (
    BriefInput,
    CalculationStatus,
    ConfidenceBand,
    EvidenceSet,
    PenaltyContribution,
    ProfileEvidence,
    ProfileSnapshot,
    ScoreCalculation,
    ScoreSignal,
    ScoreStage,
    ScoringConfigInput,
    SectionState,
    SignalId,
    Term,
    Verdict,
)

_QUANTUM = Decimal("0.000001")
_ZERO = Decimal(0)
_ONE = Decimal(1)
_HUNDRED = Decimal(100)


class ZeroEffectiveWeightError(ValueError):
    pass


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(_QUANTUM, rounding=ROUND_HALF_EVEN)


def _clamp_score(value: Decimal) -> Decimal:
    return min(_HUNDRED, max(_ZERO, value))


def _confidence_band(confidence: Decimal) -> ConfidenceBand:
    if confidence >= Decimal("0.8"):
        return ConfidenceBand.HIGH
    if confidence >= Decimal("0.5"):
        return ConfidenceBand.MEDIUM
    return ConfidenceBand.LOW


def negative_keyword_penalty(
    brief: BriefInput, snapshot: ProfileSnapshot
) -> PenaltyContribution | None:
    matched: list[str] = []
    evidence: list[ProfileEvidence] = []
    for keyword in brief.negative_keywords:
        term = Term(keyword)
        keyword_evidence: list[ProfileEvidence] = []
        for section in snapshot.sections:
            if section.state is not SectionState.COMPLETE:
                continue
            keyword_evidence.extend(
                evidence_for_match(section, match)
                for match in find_term_matches(section.raw_text, term)
            )
        if keyword_evidence:
            matched.append(normalize_text(keyword))
            evidence.extend(keyword_evidence)
    if not matched:
        return None
    points = min(Decimal(15), Decimal(3 * len(matched)))
    return PenaltyContribution(
        "P-1",
        points,
        tuple(sorted(matched)),
        tuple(
            sorted(
                evidence,
                key=lambda item: (
                    item.section_name,
                    item.span.start,
                    item.span.end,
                    item.matcher.value,
                ),
            )
        ),
    )


def contradiction_penalty(
    signals: tuple[ScoreSignal, ...],
) -> PenaltyContribution | None:
    claims = tuple(
        claim
        for signal in signals
        for claim in signal.claims
        if claim.verdict is Verdict.CONTRADICTED
    )
    if not claims:
        return None
    points = min(Decimal(10), Decimal(5 * len(claims)))
    evidence = tuple(
        item
        for claim in claims
        if isinstance(claim.provenance, EvidenceSet)
        for item in claim.provenance.entries
    )
    return PenaltyContribution(
        "P-2",
        points,
        tuple(sorted(claim.claim_key for claim in claims)),
        evidence,
    )


def derive_penalties(
    brief: BriefInput,
    snapshot: ProfileSnapshot,
    signals: tuple[ScoreSignal, ...],
) -> tuple[PenaltyContribution, ...]:
    penalties = (
        negative_keyword_penalty(brief, snapshot),
        contradiction_penalty(signals),
    )
    return tuple(item for item in penalties if item is not None)


def aggregate(
    *,
    active: tuple[SignalId, ...],
    signals: tuple[ScoreSignal, ...],
    config: ScoringConfigInput,
    stage: ScoreStage,
    penalties: tuple[PenaltyContribution, ...] = (),
) -> ScoreCalculation:
    """Aggregate already evaluated signals under the exact fractional bounds."""
    stage = ScoreStage(stage)
    active = tuple(
        sorted((SignalId(item) for item in active), key=lambda item: item.value)
    )
    ordered_signals = tuple(sorted(signals, key=lambda item: item.signal_id.value))
    if len(set(active)) != len(active):
        raise ValueError("active signals must be unique")
    if tuple(item.signal_id for item in ordered_signals) != active:
        raise ValueError("active signals and evaluated signals must correspond exactly")
    if len({item.penalty_id for item in penalties}) != len(penalties):
        raise ValueError("each named penalty may contribute only once")
    if not active:
        if penalties:
            raise ValueError("an all-inert result cannot evaluate penalties")
        return ScoreCalculation(
            score=None,
            score_lower=None,
            score_upper=None,
            confidence=_ZERO,
            confidence_band=ConfidenceBand.LOW,
            calculation_status=CalculationStatus.UNKNOWN,
            active_signal_count=0,
            stage=stage,
            signals=(),
            penalties=(),
        )

    with localcontext() as context:
        context.prec = 50
        total_weight = sum((config.weight_for(item) for item in active), _ZERO)
        if total_weight == 0:
            raise ZeroEffectiveWeightError(
                "at least one active signal must have positive effective weight"
            )
        observed = sum(
            (
                config.weight_for(item.signal_id)
                * item.availability
                * item.raw_subscore
                for item in ordered_signals
            ),
            _ZERO,
        )
        available_weight = sum(
            (
                config.weight_for(item.signal_id) * item.availability
                for item in ordered_signals
            ),
            _ZERO,
        )
        optimistic = sum(
            (
                config.weight_for(item.signal_id)
                * (item.availability * item.raw_subscore + _ONE - item.availability)
                for item in ordered_signals
            ),
            _ZERO,
        )
        penalty = sum((item.points for item in penalties), _ZERO)
        if available_weight == 0:
            return ScoreCalculation(
                score=None,
                score_lower=None,
                score_upper=None,
                confidence=_ZERO,
                confidence_band=None,
                calculation_status=CalculationStatus.UNKNOWN,
                active_signal_count=len(active),
                stage=stage,
                signals=ordered_signals,
                penalties=tuple(sorted(penalties, key=lambda item: item.penalty_id)),
            )
        score = _quantize(
            _clamp_score(_HUNDRED * observed / available_weight - penalty)
        )
        lower = _quantize(_clamp_score(_HUNDRED * observed / total_weight - penalty))
        upper = _quantize(_clamp_score(_HUNDRED * optimistic / total_weight - penalty))
        confidence = _quantize(available_weight / total_weight)
        return ScoreCalculation(
            score=score,
            score_lower=lower,
            score_upper=upper,
            confidence=confidence,
            confidence_band=_confidence_band(confidence),
            calculation_status=CalculationStatus.SCORED,
            active_signal_count=len(active),
            stage=stage,
            signals=ordered_signals,
            penalties=tuple(sorted(penalties, key=lambda item: item.penalty_id)),
        )


def calculate_score(
    brief: BriefInput,
    config: ScoringConfigInput,
    snapshot: ProfileSnapshot,
    *,
    stage: ScoreStage,
) -> ScoreCalculation:
    """Evaluate signals and aggregate one immutable candidate snapshot."""
    active = active_signal_ids(brief)
    if not brief.required_credentials and config.weight_for(SignalId.CREDENTIAL) != 0:
        raise InvalidCredentialWeightError(
            "credential weight must be zero when credential input is empty"
        )
    if not active:
        return aggregate(
            active=(),
            signals=(),
            config=config,
            stage=stage,
            penalties=(),
        )
    signals = evaluate_signals(brief, config, snapshot)
    penalties = derive_penalties(brief, snapshot, signals)
    return aggregate(
        active=active,
        signals=signals,
        config=config,
        stage=stage,
        penalties=penalties,
    )


__all__ = [
    "ZeroEffectiveWeightError",
    "aggregate",
    "calculate_score",
    "contradiction_penalty",
    "derive_penalties",
    "negative_keyword_penalty",
]
