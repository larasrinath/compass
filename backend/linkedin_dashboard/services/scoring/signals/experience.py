from __future__ import annotations

from decimal import Decimal

from linkedin_dashboard.services.scoring.matching import find_term_matches
from linkedin_dashboard.services.scoring.signals.common import (
    coverage_set,
    evidence_for_match,
    evidence_for_source,
    missing_set,
    unparseable_set,
)
from linkedin_dashboard.services.scoring.types import (
    BriefInput,
    EvidenceSet,
    ExperienceRole,
    MonthsDerivation,
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
    if not terms:
        return (evidence_for_source(role.title),)
    values = (
        (role.title,) if role.description is None else (role.title, role.description)
    )
    evidence: list[ProfileEvidence] = []
    for value in values:
        section = snapshot.section(value.section_name)
        if section is None or section.state is not SectionState.COMPLETE:
            continue
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


def _duration_evidence(role: ExperienceRole) -> tuple[ProfileEvidence, ...]:
    if role.months is None or role.months_derivation is None:
        return ()
    source = (
        role.date_range
        if role.months_derivation is MonthsDerivation.DATE_RANGE
        else role.duration
    )
    if source is None:
        raise AssertionError("validated month derivation lacks its source")
    return (evidence_for_source(source),)


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

    verified_evidence: list[ProfileEvidence] = []
    relevant_months = 0
    unparsed_roles = 0
    if not snapshot.experience_roles:
        claim = ScoreClaim(
            claim_key="S-3:experience-depth",
            display_term=f"{required} months relevant experience",
            verdict=Verdict.UNKNOWN,
            provenance=unparseable_set("experience"),
        )
        return ScoreSignal(
            SignalId.EXPERIENCE,
            Rollup.UNKNOWN,
            Decimal(0),
            Decimal("0.5"),
            (claim,),
        )
    for role in snapshot.experience_roles:
        role_evidence = _role_evidence(snapshot, role, brief)
        if not role_evidence:
            continue
        if role.months is None:
            unparsed_roles += 1
            continue
        relevant_months += role.months
        verified_evidence.extend((*role_evidence, *_duration_evidence(role)))

    score = min(Decimal(1), Decimal(relevant_months) / Decimal(required))
    display = f"{required} months relevant experience"
    if score >= 1 and verified_evidence:
        claim = ScoreClaim(
            claim_key="S-3:experience-depth",
            display_term=display,
            verdict=Verdict.MATCHED,
            provenance=EvidenceSet(tuple(verified_evidence)),
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
    elif verified_evidence:
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
            for item in verified_evidence
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
