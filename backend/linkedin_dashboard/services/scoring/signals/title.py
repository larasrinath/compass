from __future__ import annotations

from decimal import Decimal

from linkedin_dashboard.services.scoring.matching import normalize_text, word_tokens
from linkedin_dashboard.services.scoring.signals.common import (
    availability_for,
    coverage_set,
    missing_set,
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
    SignalId,
    SourcedText,
    Term,
    Verdict,
)

_SECTIONS = ("main_profile", "experience")


def _similarity(left: str, right: str) -> Decimal:
    left_tokens = set(word_tokens(left))
    right_tokens = set(word_tokens(right))
    if not left_tokens or not right_tokens:
        return Decimal(0)
    overlap = len(left_tokens.intersection(right_tokens))
    return Decimal(2 * overlap) / Decimal(len(left_tokens) + len(right_tokens))


def title_similarity(brief: BriefInput, snapshot: ProfileSnapshot) -> ScoreSignal:
    if not brief.target_titles:
        raise ValueError("title cannot evaluate an inert signal")
    candidates: list[tuple[Decimal, Term, SourcedText]] = []
    for source in snapshot.titles:
        for target in brief.target_titles:
            similarity = _similarity(source.text, target.term)
            candidates.append((similarity, target, source))
    candidates.sort(
        key=lambda item: (
            -item[0],
            normalize_text(item[1].term),
            item[2].section_name,
            item[2].span.start,
        )
    )
    best = candidates[0] if candidates else None

    score = Decimal(0) if best is None else best[0]
    if best is not None and score > 0:
        source = best[2]
        target = best[1]
        matcher = (
            Matcher.EXACT
            if normalize_text(source.text) == normalize_text(target.term)
            else Matcher.STEM
        )
        evidence = ProfileEvidence(
            matched_term=source.text,
            matcher=matcher,
            section_name=source.section_name,
            profile_section_id=source.section_id,
            content_sha256=source.content_sha256,
            span=source.span,
        )
        claim = ScoreClaim(
            "S-4:title-similarity",
            target.term,
            Verdict.MATCHED,
            EvidenceSet((evidence,)),
        )
    elif availability_for(snapshot, _SECTIONS) == 1:
        claim = ScoreClaim(
            "S-4:title-similarity",
            ", ".join(item.term for item in brief.target_titles),
            Verdict.NOT_MATCHED,
            coverage_set(snapshot, _SECTIONS, brief.target_titles),
        )
    else:
        claim = ScoreClaim(
            "S-4:title-similarity",
            ", ".join(item.term for item in brief.target_titles),
            Verdict.UNKNOWN,
            missing_set(snapshot, _SECTIONS),
        )
    return ScoreSignal(
        SignalId.TITLE,
        Rollup(claim.verdict.value),
        score,
        availability_for(snapshot, _SECTIONS),
        (claim,),
    )
