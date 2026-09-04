from __future__ import annotations

from decimal import Decimal

from linkedin_dashboard.services.scoring.matching import find_term_matches
from linkedin_dashboard.services.scoring.signals.common import (
    coverage_set,
    evidence_for_match,
    missing_set,
    unparseable_set,
)
from linkedin_dashboard.services.scoring.types import (
    BriefInput,
    EvidenceSet,
    ExperienceRole,
    Polarity,
    ProfileEvidence,
    ProfileSnapshot,
    Rollup,
    ScoreClaim,
    ScoreSignal,
    SectionState,
    SignalId,
    Verdict,
)

_SECTIONS = ("experience",)


def _role_evidence(
    snapshot: ProfileSnapshot,
    role: ExperienceRole,
    brief: BriefInput,
) -> tuple[ProfileEvidence, ...]:
    terms = (*brief.target_titles, *brief.required_skills)
    section = snapshot.section("experience")
    if section is None or section.state is not SectionState.COMPLETE:
        return ()
    values = (
        (role.title,) if role.description is None else (role.title, role.description)
    )
    evidence: list[ProfileEvidence] = []
    for value in values:
        for term in terms:
            evidence.extend(
                evidence_for_match(section, match)
                for match in find_term_matches(
                    section.raw_text,
                    term,
                    region_start=value.span.start,
                    region_end=value.span.end,
                )
            )
    unique = {
        (item.span.start, item.span.end, item.matcher.value): item for item in evidence
    }
    return tuple(unique[key] for key in sorted(unique))


def relevant_experience(brief: BriefInput, snapshot: ProfileSnapshot) -> ScoreSignal:
    required = brief.required_experience_months
    if required is None or required <= 0:
        raise ValueError("experience cannot evaluate an inert signal")
    section = snapshot.section("experience")
    if section is None or section.state is SectionState.MISSING:
        claim = ScoreClaim(
            claim_key="S-3:experience-depth",
            display_term=f"{required} months relevant experience",
            verdict=Verdict.UNKNOWN,
            provenance=missing_set(snapshot, _SECTIONS),
        )
        return ScoreSignal(
            SignalId.EXPERIENCE,
            Rollup.UNKNOWN,
            Decimal(0),
            Decimal(0),
            (claim,),
        )

    evidence: list[ProfileEvidence] = []
    relevant_months = 0
    unparsed_roles = sum(role.months is None for role in snapshot.experience_roles)
    for role in snapshot.experience_roles:
        role_evidence = _role_evidence(snapshot, role, brief)
        if not role_evidence:
            continue
        evidence.extend(role_evidence)
        if role.months is None:
            continue
        else:
            relevant_months += role.months

    score = min(Decimal(1), Decimal(relevant_months) / Decimal(required))
    display = f"{required} months relevant experience"
    if score >= 1 and evidence:
        claim = ScoreClaim(
            claim_key="S-3:experience-depth",
            display_term=display,
            verdict=Verdict.MATCHED,
            provenance=EvidenceSet(tuple(evidence)),
        )
        availability = Decimal("0.5") if unparsed_roles else Decimal(1)
    elif unparsed_roles:
        claim = ScoreClaim(
            claim_key="S-3:experience-depth",
            display_term=display,
            verdict=Verdict.UNKNOWN,
            provenance=unparseable_set("experience"),
        )
        availability = Decimal("0.5")
    elif evidence:
        contradicting = tuple(
            ProfileEvidence(
                matched_term=item.matched_term,
                matcher=item.matcher,
                section_name=item.section_name,
                profile_section_id=item.profile_section_id,
                content_sha256=item.content_sha256,
                span=item.span,
                polarity=Polarity.CONTRADICTING,
            )
            for item in evidence
        )
        claim = ScoreClaim(
            claim_key="S-3:experience-depth",
            display_term=display,
            verdict=Verdict.CONTRADICTED,
            provenance=EvidenceSet(contradicting),
        )
        availability = Decimal(1)
    else:
        terms = (*brief.target_titles, *brief.required_skills)
        claim = ScoreClaim(
            claim_key="S-3:experience-depth",
            display_term=display,
            verdict=Verdict.NOT_MATCHED,
            provenance=coverage_set(snapshot, _SECTIONS, terms),
        )
        availability = Decimal(1)
    return ScoreSignal(
        SignalId.EXPERIENCE,
        Rollup(claim.verdict.value),
        score,
        availability,
        (claim,),
    )
