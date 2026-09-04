"""Signal activity and deterministic evaluators."""

from __future__ import annotations

from linkedin_dashboard.services.scoring.signals.credentials import (
    credential_requirement,
)
from linkedin_dashboard.services.scoring.signals.experience import relevant_experience
from linkedin_dashboard.services.scoring.signals.industry import industry_relevance
from linkedin_dashboard.services.scoring.signals.location import location_fit
from linkedin_dashboard.services.scoring.signals.skills import (
    optional_skills,
    required_skills,
)
from linkedin_dashboard.services.scoring.signals.title import title_similarity
from linkedin_dashboard.services.scoring.types import (
    BriefInput,
    ProfileSnapshot,
    ScoreSignal,
    ScoringConfigInput,
    SignalId,
)


class InvalidCredentialWeightError(ValueError):
    pass


def active_signal_ids(brief: BriefInput) -> tuple[SignalId, ...]:
    active: list[SignalId] = []
    if brief.required_skills:
        active.append(SignalId.REQUIRED_SKILLS)
    if brief.optional_skills:
        active.append(SignalId.OPTIONAL_SKILLS)
    if brief.required_experience_months is not None and (
        brief.required_experience_months > 0
    ):
        active.append(SignalId.EXPERIENCE)
    if brief.target_titles:
        active.append(SignalId.TITLE)
    if brief.industries:
        active.append(SignalId.INDUSTRY)
    if brief.location:
        active.append(SignalId.LOCATION)
    if brief.required_credentials:
        active.append(SignalId.CREDENTIAL)
    return tuple(active)


def evaluate_signals(
    brief: BriefInput,
    config: ScoringConfigInput,
    snapshot: ProfileSnapshot,
) -> tuple[ScoreSignal, ...]:
    active = active_signal_ids(brief)
    if (
        SignalId.CREDENTIAL not in active
        and config.weight_for(SignalId.CREDENTIAL) != 0
    ):
        raise InvalidCredentialWeightError(
            "credential weight must be zero when credential input is empty"
        )
    output: list[ScoreSignal] = []
    for signal_id in active:
        if signal_id is SignalId.REQUIRED_SKILLS:
            output.append(required_skills(brief.required_skills, snapshot))
        elif signal_id is SignalId.OPTIONAL_SKILLS:
            output.append(optional_skills(brief.optional_skills, snapshot))
        elif signal_id is SignalId.EXPERIENCE:
            output.append(relevant_experience(brief, snapshot))
        elif signal_id is SignalId.TITLE:
            output.append(title_similarity(brief, snapshot))
        elif signal_id is SignalId.INDUSTRY:
            output.append(industry_relevance(brief, snapshot))
        elif signal_id is SignalId.LOCATION:
            output.append(location_fit(brief, config, snapshot))
        elif signal_id is SignalId.CREDENTIAL:
            output.append(credential_requirement(brief.required_credentials, snapshot))
    return tuple(output)


__all__ = [
    "InvalidCredentialWeightError",
    "active_signal_ids",
    "evaluate_signals",
]
