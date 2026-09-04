"""M4 scoring lifecycle and immutable configuration service."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from linkedin_dashboard.correlation import current_correlation_id
from linkedin_dashboard.db.models import (
    AuditLog,
    Candidate,
    CandidateScore,
    DashboardSession,
    RoleBrief,
    ScoringConfig,
)
from linkedin_dashboard.db.session import Database
from linkedin_dashboard.services.brief import contains_protected_criterion
from linkedin_dashboard.services.scoring.normalization import normalize_text
from linkedin_dashboard.services.scoring.signals import active_signal_ids
from linkedin_dashboard.services.scoring.types import DEFAULT_WEIGHTS, SignalId
from linkedin_dashboard.services.scoring_persist import (
    calculate_and_persist,
    load_kernel_brief,
)

DEFAULT_WEIGHT_MAP = {
    item.signal_id.value: float(item.value) for item in DEFAULT_WEIGHTS
}


class ConfigVersionConflict(RuntimeError):
    pass


class ScoringValidationError(ValueError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _normalized_metros(value: dict[str, list[str]]) -> dict[str, list[str]]:
    if len(value) > 100:
        raise ScoringValidationError("at most 100 metro equivalences are allowed")
    output: dict[str, list[str]] = {}
    for raw_name, raw_locations in value.items():
        if not isinstance(raw_name, str) or len(raw_name) > 240:
            raise ScoringValidationError("metro names must be at most 240 characters")
        if not isinstance(raw_locations, list) or len(raw_locations) > 100:
            raise ScoringValidationError(
                "each metro may contain at most 100 equivalent locations"
            )
        if any(not isinstance(item, str) or len(item) > 240 for item in raw_locations):
            raise ScoringValidationError(
                "metro locations must be strings of at most 240 characters"
            )
        if contains_protected_criterion(raw_name) or any(
            contains_protected_criterion(item) for item in raw_locations
        ):
            raise ScoringValidationError(
                "protected attributes cannot be used in metro equivalences"
            )
        name = " ".join(raw_name.strip().split())
        if not name:
            continue
        locations = sorted(
            {
                " ".join(item.strip().split())
                for item in raw_locations
                if " ".join(item.strip().split())
            },
            key=str.casefold,
        )
        canonical = normalize_text(name)
        if any(normalize_text(existing) == canonical for existing in output):
            raise ScoringValidationError("duplicate normalized metro equivalence")
        output[name] = locations
    return dict(sorted(output.items(), key=lambda item: item[0].casefold()))


class ScoringService:
    def __init__(self, database: Database) -> None:
        self.database = database
        self._transition_lock = database.transition_lock

    def _current_config(
        self, session: Session, session_id: str
    ) -> ScoringConfig | None:
        return session.scalar(
            select(ScoringConfig)
            .where(
                ScoringConfig.session_id == session_id,
                ScoringConfig.superseded_at.is_(None),
            )
            .order_by(ScoringConfig.version.desc())
            .limit(1)
        )

    def ensure_default_config(self, session: Session, session_id: str) -> ScoringConfig:
        current = self._current_config(session, session_id)
        if current is not None:
            return current
        if session.get(DashboardSession, session_id) is None:
            raise LookupError("session does not exist")
        current = ScoringConfig(
            id=str(uuid4()),
            session_id=session_id,
            version=1,
            created_at=_now(),
            weights=dict(DEFAULT_WEIGHT_MAP),
            metro_region_equivalences={},
            superseded_at=None,
        )
        session.add(current)
        session.flush()
        return current

    def current_config(self, session_id: str | None = None) -> ScoringConfig:
        with self._transition_lock:
            with self.database.sessions.begin() as session:
                if session_id is None:
                    dashboard_session = session.scalar(
                        select(DashboardSession)
                        .where(
                            DashboardSession.id
                            != "00000000-0000-0000-0000-000000000000"
                        )
                        .order_by(
                            DashboardSession.created_at.desc(),
                            DashboardSession.id.desc(),
                        )
                        .limit(1)
                    )
                    if dashboard_session is None:
                        raise LookupError("session does not exist")
                    session_id = dashboard_session.id
                config = self.ensure_default_config(session, session_id)
                session.expunge(config)
                return config

    def config_record(self, session_id: str | None = None) -> dict[str, Any]:
        config = self.current_config(session_id)
        with self.database.sessions() as session:
            brief = session.scalar(
                select(RoleBrief)
                .where(
                    RoleBrief.session_id == config.session_id,
                    RoleBrief.superseded_at.is_(None),
                )
                .order_by(RoleBrief.version.desc())
                .limit(1)
            )
            active = (
                active_signal_ids(load_kernel_brief(session, brief))
                if brief is not None
                else ()
            )
        active_values = {item.value for item in active}
        labels = {
            "S-1": "required skills",
            "S-2": "optional skills",
            "S-3": "required experience",
            "S-4": "target titles",
            "S-5": "industries",
            "S-6": "target location",
            "S-8": "required credentials",
        }
        return {
            "version": str(config.version),
            "weights": config.weights,
            "active_signal_ids": sorted(active_values),
            "inert_reasons": {
                signal_id: {
                    "code": "brief_input_empty",
                    "message": (
                        "Saved, not currently applied: "
                        f"{labels[signal_id]} input is empty."
                    ),
                }
                for signal_id in DEFAULT_WEIGHT_MAP
                if signal_id not in active_values
            },
            "metro_region_equivalences": config.metro_region_equivalences,
        }

    def _validate_weights(
        self,
        session: Session,
        *,
        session_id: str,
        weights: dict[str, float],
    ) -> dict[str, float]:
        if set(weights) != set(DEFAULT_WEIGHT_MAP):
            raise ScoringValidationError(
                "weights must contain exactly S-1 through S-6 and S-8"
            )
        normalized: dict[str, float] = {}
        for key, value in weights.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ScoringValidationError(f"{key} weight must be numeric")
            number = float(value)
            if not math.isfinite(number) or number < 0 or number > 1_000_000:
                raise ScoringValidationError(
                    f"{key} weight must be finite and between 0 and 1000000"
                )
            normalized[key] = number
        brief = session.scalar(
            select(RoleBrief)
            .where(
                RoleBrief.session_id == session_id,
                RoleBrief.superseded_at.is_(None),
            )
            .order_by(RoleBrief.version.desc())
            .limit(1)
        )
        if brief is None:
            if normalized[SignalId.CREDENTIAL.value] != 0:
                raise ScoringValidationError("S-8 requires a current credential")
            return normalized
        active = active_signal_ids(load_kernel_brief(session, brief))
        if (
            SignalId.CREDENTIAL not in active
            and normalized[SignalId.CREDENTIAL.value] != 0
        ):
            raise ScoringValidationError("S-8 requires a current credential")
        if active and not any(normalized[item.value] > 0 for item in active):
            raise ScoringValidationError(
                "at least one active scoring signal must have positive weight"
            )
        return normalized

    def update_config(
        self,
        *,
        expected_version: str,
        weights: dict[str, float],
        metro_region_equivalences: dict[str, list[str]],
        session_id: str | None = None,
    ) -> dict[str, Any]:
        # Keep optimistic version checks deterministic for concurrent requests
        # in this single-process dashboard.  The DB uniqueness guard remains
        # the fail-closed boundary for any second process or raw writer.
        with self._transition_lock:
            return self._update_config_locked(
                expected_version=expected_version,
                weights=weights,
                metro_region_equivalences=metro_region_equivalences,
                session_id=session_id,
            )

    def _update_config_locked(
        self,
        *,
        expected_version: str,
        weights: dict[str, float],
        metro_region_equivalences: dict[str, list[str]],
        session_id: str | None = None,
    ) -> dict[str, Any]:
        with self.database.sessions.begin() as session:
            if session_id is None:
                dashboard_session = session.scalar(
                    select(DashboardSession)
                    .order_by(
                        DashboardSession.created_at.desc(), DashboardSession.id.desc()
                    )
                    .limit(1)
                )
                if dashboard_session is None:
                    raise LookupError("session does not exist")
                session_id = dashboard_session.id
            current = self.ensure_default_config(session, session_id)
            if str(current.version) != expected_version:
                raise ConfigVersionConflict("scoring configuration changed; reload it")
            normalized = self._validate_weights(
                session, session_id=session_id, weights=weights
            )
            normalized_metros = _normalized_metros(metro_region_equivalences)
            now = _now()
            current.superseded_at = now
            replacement = ScoringConfig(
                id=str(uuid4()),
                session_id=session_id,
                version=current.version + 1,
                created_at=now,
                weights=normalized,
                metro_region_equivalences=normalized_metros,
                superseded_at=None,
            )
            session.add(replacement)
            session.flush()
            self._rescore_session(session, session_id, replacement)
            session.add(
                AuditLog(
                    session_id=session_id,
                    at=now,
                    actor="operator",
                    action="scoring_config.saved",
                    subject_type="scoring_config",
                    subject_id=replacement.id,
                    detail={
                        "version": replacement.version,
                        "expected_version": expected_version,
                    },
                    correlation_id=current_correlation_id(),
                )
            )
        return self.config_record(session_id)

    def on_brief_saved(
        self,
        session: Session,
        *,
        previous: RoleBrief | None,
        current: RoleBrief,
        removed_final_credential: bool,
    ) -> None:
        config = self.ensure_default_config(session, current.session_id)
        if removed_final_credential and config.weights.get("S-8", 0) != 0:
            now = _now()
            config.superseded_at = now
            weights = dict(config.weights)
            weights["S-8"] = 0.0
            config = ScoringConfig(
                id=str(uuid4()),
                session_id=current.session_id,
                version=config.version + 1,
                created_at=now,
                weights=weights,
                metro_region_equivalences=config.metro_region_equivalences,
                superseded_at=None,
            )
            session.add(config)
            session.flush()
        self._validate_weights(
            session, session_id=current.session_id, weights=dict(config.weights)
        )
        if previous is not None:
            self._rescore_session(session, current.session_id, config, brief=current)

    def _rescore_session(
        self,
        session: Session,
        session_id: str,
        config: ScoringConfig,
        *,
        brief: RoleBrief | None = None,
    ) -> list[CandidateScore]:
        if brief is None:
            brief = session.scalar(
                select(RoleBrief)
                .where(
                    RoleBrief.session_id == session_id,
                    RoleBrief.superseded_at.is_(None),
                )
                .order_by(RoleBrief.version.desc())
                .limit(1)
            )
        if brief is None:
            return []
        candidates = list(
            session.scalars(
                select(Candidate)
                .where(Candidate.session_id == session_id)
                .order_by(Candidate.id)
            )
        )
        return [
            calculate_and_persist(
                session, candidate=candidate, brief=brief, config=config
            )
            for candidate in candidates
        ]

    def rescore_candidate(self, candidate_id: str) -> CandidateScore:
        with self._transition_lock:
            with self.database.sessions.begin() as session:
                row = self.rescore_candidate_in_session(session, candidate_id)
                session.expunge(row)
                return row

    def rescore_candidate_in_session(
        self, session: Session, candidate_id: str
    ) -> CandidateScore:
        with self._transition_lock:
            candidate = session.get(Candidate, candidate_id)
            if candidate is None:
                raise LookupError("candidate does not exist")
            brief = session.scalar(
                select(RoleBrief)
                .where(
                    RoleBrief.session_id == candidate.session_id,
                    RoleBrief.superseded_at.is_(None),
                )
                .order_by(RoleBrief.version.desc())
                .limit(1)
            )
            if brief is None:
                raise LookupError("candidate session has no brief")
            config = self.ensure_default_config(session, candidate.session_id)
            return calculate_and_persist(
                session, candidate=candidate, brief=brief, config=config
            )
