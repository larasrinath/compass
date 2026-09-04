"""Shared deterministic signal helpers."""

from __future__ import annotations

from decimal import Decimal

from linkedin_dashboard.services.scoring.matching import (
    MATCHER_VERSION,
    TermMatch,
    find_term_matches,
    normalize_text,
)
from linkedin_dashboard.services.scoring.types import (
    AbsenceCoverage,
    CoverageSet,
    EvidenceSet,
    Matcher,
    MissingReason,
    MissingSection,
    MissingSet,
    Polarity,
    ProfileEvidence,
    ProfileSection,
    ProfileSnapshot,
    Rollup,
    ScoreClaim,
    SectionState,
    SourcedText,
    Term,
    Verdict,
)


def required_sections(
    snapshot: ProfileSnapshot, names: tuple[str, ...]
) -> tuple[ProfileSection | None, ...]:
    return tuple(snapshot.section(name) for name in names)


def availability_for(snapshot: ProfileSnapshot, names: tuple[str, ...]) -> Decimal:
    sections = required_sections(snapshot, names)
    completed = sum(
        item is not None and item.state is SectionState.COMPLETE for item in sections
    )
    if completed == len(names):
        return Decimal(1)
    if completed:
        return Decimal("0.5")
    return Decimal(0)


def missing_set(snapshot: ProfileSnapshot, names: tuple[str, ...]) -> MissingSet:
    entries: list[MissingSection] = []
    for name, section in zip(names, required_sections(snapshot, names), strict=True):
        if section is None:
            entries.append(MissingSection(name, MissingReason.NOT_REQUESTED))
        elif section.state is SectionState.MISSING:
            if section.missing_reason is None:
                raise AssertionError("validated missing section lacks a reason")
            entries.append(
                MissingSection(
                    name,
                    section.missing_reason,
                    section.section_error_id,
                )
            )
    return MissingSet(tuple(entries))


def unparseable_set(section_name: str) -> MissingSet:
    return MissingSet((MissingSection(section_name, MissingReason.UNPARSEABLE),))


def coverage_set(
    snapshot: ProfileSnapshot,
    names: tuple[str, ...],
    terms: tuple[Term, ...],
) -> CoverageSet:
    normalized_terms = tuple(normalize_text(item.term) for item in terms)
    aliases = tuple(
        sorted(
            normalize_text(alias)
            for item in terms
            for alias in item.aliases
            if normalize_text(alias)
        )
    )
    entries: list[AbsenceCoverage] = []
    for section in required_sections(snapshot, names):
        if section is None or section.state is not SectionState.COMPLETE:
            raise ValueError("coverage requires every searched section to be complete")
        entries.append(
            AbsenceCoverage(
                profile_section_id=section.section_id,
                section_name=section.name,
                content_sha256=section.content_sha256,
                normalized_terms=normalized_terms,
                aliases=aliases,
                matcher_version=MATCHER_VERSION,
            )
        )
    return CoverageSet(tuple(entries))


def evidence_for_match(
    section: ProfileSection,
    match: TermMatch,
    *,
    polarity: Polarity = Polarity.SUPPORTING,
) -> ProfileEvidence:
    return ProfileEvidence(
        matched_term=match.matched_term,
        matcher=match.matcher,
        section_name=section.name,
        profile_section_id=section.section_id,
        content_sha256=section.content_sha256,
        span=match.span,
        polarity=polarity,
    )


def evidence_for_source(
    source: SourcedText,
    *,
    matcher: Matcher = Matcher.EXACT,
    polarity: Polarity = Polarity.SUPPORTING,
) -> ProfileEvidence:
    return ProfileEvidence(
        matched_term=source.text,
        matcher=matcher,
        section_name=source.section_name,
        profile_section_id=source.section_id,
        content_sha256=source.content_sha256,
        span=source.span,
        polarity=polarity,
    )


def evidence_for_term(
    snapshot: ProfileSnapshot,
    names: tuple[str, ...],
    term: Term,
) -> tuple[ProfileEvidence, ...]:
    entries: list[ProfileEvidence] = []
    for section in required_sections(snapshot, names):
        if section is None or section.state is not SectionState.COMPLETE:
            continue
        entries.extend(
            evidence_for_match(section, match)
            for match in find_term_matches(section.raw_text, term)
        )
    return tuple(
        sorted(
            entries,
            key=lambda item: (
                item.section_name,
                item.span.start,
                item.span.end,
                item.matcher.value,
            ),
        )
    )


def term_claim(
    *,
    signal_id: str,
    snapshot: ProfileSnapshot,
    names: tuple[str, ...],
    term: Term,
) -> ScoreClaim:
    evidence = evidence_for_term(snapshot, names, term)
    if evidence:
        return ScoreClaim(
            claim_key=f"{signal_id}:{normalize_text(term.term)}",
            display_term=term.term,
            verdict=Verdict.MATCHED,
            provenance=EvidenceSet(evidence),
        )
    availability = availability_for(snapshot, names)
    if availability == 1:
        return ScoreClaim(
            claim_key=f"{signal_id}:{normalize_text(term.term)}",
            display_term=term.term,
            verdict=Verdict.NOT_MATCHED,
            provenance=coverage_set(snapshot, names, (term,)),
        )
    return ScoreClaim(
        claim_key=f"{signal_id}:{normalize_text(term.term)}",
        display_term=term.term,
        verdict=Verdict.UNKNOWN,
        provenance=missing_set(snapshot, names),
    )


def rollup_for(claims: tuple[ScoreClaim, ...]) -> Rollup:
    verdicts = {item.verdict for item in claims}
    if len(verdicts) != 1:
        return Rollup.MIXED
    return Rollup(next(iter(verdicts)).value)
