from __future__ import annotations

from decimal import Decimal

from linkedin_dashboard.services.scoring.signals.common import (
    availability_for,
    coverage_set,
    evidence_for_term,
    missing_set,
)
from linkedin_dashboard.services.scoring.types import (
    BriefInput,
    EvidenceSet,
    ProfileSnapshot,
    Rollup,
    ScoreClaim,
    ScoreSignal,
    SignalId,
    Verdict,
)

_SECTIONS = ("experience", "main_profile")


def industry_relevance(brief: BriefInput, snapshot: ProfileSnapshot) -> ScoreSignal:
    if not brief.industries:
        raise ValueError("industry cannot evaluate an inert signal")
    evidence = tuple(
        item
        for term in brief.industries
        for item in evidence_for_term(snapshot, _SECTIONS, term)
    )
    matched_terms = sum(
        bool(evidence_for_term(snapshot, _SECTIONS, term)) for term in brief.industries
    )
    score = Decimal(matched_terms) / Decimal(len(brief.industries))
    display = ", ".join(item.term for item in brief.industries)
    if evidence:
        claim = ScoreClaim(
            "S-5:industry-relevance",
            display,
            Verdict.MATCHED,
            EvidenceSet(evidence),
        )
    elif availability_for(snapshot, _SECTIONS) == 1:
        claim = ScoreClaim(
            "S-5:industry-relevance",
            display,
            Verdict.NOT_MATCHED,
            coverage_set(snapshot, _SECTIONS, brief.industries),
        )
    else:
        claim = ScoreClaim(
            "S-5:industry-relevance",
            display,
            Verdict.UNKNOWN,
            missing_set(snapshot, _SECTIONS),
        )
    return ScoreSignal(
        SignalId.INDUSTRY,
        Rollup(claim.verdict.value),
        score,
        availability_for(snapshot, _SECTIONS),
        (claim,),
    )
