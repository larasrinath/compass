from __future__ import annotations

from decimal import Decimal

from linkedin_dashboard.services.scoring.signals.common import (
    availability_for,
    rollup_for,
    term_claim,
)
from linkedin_dashboard.services.scoring.types import (
    ProfileSnapshot,
    ScoreSignal,
    SignalId,
    Term,
    Verdict,
)

_SECTIONS = ("skills", "experience", "main_profile")


def evaluate_term_coverage(
    signal_id: SignalId,
    terms: tuple[Term, ...],
    snapshot: ProfileSnapshot,
    *,
    sections: tuple[str, ...] = _SECTIONS,
) -> ScoreSignal:
    if not terms:
        raise ValueError("term coverage cannot evaluate an inert signal")
    claims = tuple(
        term_claim(
            signal_id=signal_id.value,
            snapshot=snapshot,
            names=sections,
            term=term,
        )
        for term in terms
    )
    matched = sum(item.verdict is Verdict.MATCHED for item in claims)
    return ScoreSignal(
        signal_id=signal_id,
        rollup=rollup_for(claims),
        raw_subscore=Decimal(matched) / Decimal(len(claims)),
        availability=availability_for(snapshot, sections),
        claims=claims,
    )


def required_skills(terms: tuple[Term, ...], snapshot: ProfileSnapshot) -> ScoreSignal:
    return evaluate_term_coverage(SignalId.REQUIRED_SKILLS, terms, snapshot)


def optional_skills(terms: tuple[Term, ...], snapshot: ProfileSnapshot) -> ScoreSignal:
    return evaluate_term_coverage(SignalId.OPTIONAL_SKILLS, terms, snapshot)
