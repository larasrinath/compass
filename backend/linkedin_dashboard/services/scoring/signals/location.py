from __future__ import annotations

from decimal import Decimal

from linkedin_dashboard.services.scoring.matching import normalize_text
from linkedin_dashboard.services.scoring.signals.common import (
    coverage_set,
    missing_set,
    unparseable_set,
)
from linkedin_dashboard.services.scoring.types import (
    BriefInput,
    EvidenceSet,
    Matcher,
    ProfileEvidence,
    ProfileSnapshot,
    Rollup,
    ScoreClaim,
    ScoreSignal,
    ScoringConfigInput,
    SectionState,
    SignalId,
    Term,
    Verdict,
)

_SECTIONS = ("main_profile",)


def _same_metro(left: str, right: str, config: ScoringConfigInput) -> bool:
    left_key = normalize_text(left)
    right_key = normalize_text(right)
    return any(
        left_key in {normalize_text(value) for value in item.locations}
        and right_key in {normalize_text(value) for value in item.locations}
        for item in config.metro_equivalences
    )


def location_fit(
    brief: BriefInput,
    config: ScoringConfigInput,
    snapshot: ProfileSnapshot,
) -> ScoreSignal:
    if not brief.location:
        raise ValueError("location cannot evaluate an inert signal")
    section = snapshot.section("main_profile")
    if section is None or section.state is SectionState.MISSING:
        claim = ScoreClaim(
            "S-6:location-fit",
            brief.location,
            Verdict.UNKNOWN,
            missing_set(snapshot, _SECTIONS),
        )
        return ScoreSignal(
            SignalId.LOCATION,
            Rollup.UNKNOWN,
            Decimal(0),
            Decimal(0),
            (claim,),
        )
    if snapshot.location is None:
        claim = ScoreClaim(
            "S-6:location-fit",
            brief.location,
            Verdict.UNKNOWN,
            unparseable_set("main_profile"),
        )
        return ScoreSignal(
            SignalId.LOCATION,
            Rollup.UNKNOWN,
            Decimal(0),
            Decimal(1),
            (claim,),
        )

    observed = snapshot.location
    exact = normalize_text(observed.text) == normalize_text(brief.location)
    metro = not exact and _same_metro(observed.text, brief.location, config)
    if exact or metro:
        evidence = ProfileEvidence(
            matched_term=observed.text,
            matcher=Matcher.EXACT if exact else Matcher.ALIAS,
            section_name=observed.section_name,
            profile_section_id=observed.section_id,
            content_sha256=observed.content_sha256,
            span=observed.span,
        )
        claim = ScoreClaim(
            "S-6:location-fit",
            brief.location,
            Verdict.MATCHED,
            EvidenceSet((evidence,)),
        )
        score = Decimal(1) if exact else Decimal("0.6")
    else:
        claim = ScoreClaim(
            "S-6:location-fit",
            brief.location,
            Verdict.NOT_MATCHED,
            coverage_set(snapshot, _SECTIONS, (Term(brief.location),)),
        )
        score = Decimal(0)
    return ScoreSignal(
        SignalId.LOCATION,
        Rollup(claim.verdict.value),
        score,
        Decimal(1),
        (claim,),
    )
