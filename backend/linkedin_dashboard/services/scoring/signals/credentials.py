from __future__ import annotations

from linkedin_dashboard.services.scoring.signals.skills import evaluate_term_coverage
from linkedin_dashboard.services.scoring.types import (
    ProfileSnapshot,
    ScoreSignal,
    SignalId,
    Term,
)

_SECTIONS = ("education", "certifications")


def credential_requirement(
    terms: tuple[Term, ...], snapshot: ProfileSnapshot
) -> ScoreSignal:
    return evaluate_term_coverage(
        SignalId.CREDENTIAL,
        terms,
        snapshot,
        sections=_SECTIONS,
    )
