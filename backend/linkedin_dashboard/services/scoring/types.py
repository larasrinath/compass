"""Immutable, API-neutral values used by the pure scoring kernel."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from decimal import Decimal, DecimalException
from enum import StrEnum
from typing import Literal

from linkedin_dashboard.parsing.spans import VerifiedSpan


class SignalId(StrEnum):
    REQUIRED_SKILLS = "S-1"
    OPTIONAL_SKILLS = "S-2"
    EXPERIENCE = "S-3"
    TITLE = "S-4"
    INDUSTRY = "S-5"
    LOCATION = "S-6"
    CREDENTIAL = "S-8"


SIGNAL_ORDER = tuple(SignalId)


class Verdict(StrEnum):
    MATCHED = "matched"
    NOT_MATCHED = "not_matched"
    UNKNOWN = "unknown"
    CONTRADICTED = "contradicted"


class Rollup(StrEnum):
    MATCHED = "matched"
    NOT_MATCHED = "not_matched"
    UNKNOWN = "unknown"
    CONTRADICTED = "contradicted"
    MIXED = "mixed"


class Matcher(StrEnum):
    EXACT = "exact"
    ALIAS = "alias"
    STEM = "stem"
    LLM_VERIFIED = "llm_verified"


class Polarity(StrEnum):
    SUPPORTING = "supporting"
    CONTRADICTING = "contradicting"


class MissingReason(StrEnum):
    NOT_REQUESTED = "not_requested"
    RATE_LIMIT = "rate_limit"
    FETCH_ERROR = "fetch_error"
    UNPARSEABLE = "unparseable"


class SectionState(StrEnum):
    COMPLETE = "complete"
    MISSING = "missing"


class ScoreStage(StrEnum):
    PROVISIONAL = "provisional"
    ENRICHED = "enriched"


class MonthsDerivation(StrEnum):
    DATE_RANGE = "date_range"
    DURATION_TEXT = "duration_text"


class ConfidenceBand(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CalculationStatus(StrEnum):
    SCORED = "scored"
    UNKNOWN = "unknown"


MAX_SIGNAL_WEIGHT = Decimal("1000000")
_ZERO_DECIMAL = Decimal(0)


def _decimal_value(value: object, field: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric and finite")
    try:
        result = Decimal(str(value))
    except (DecimalException, ValueError) as exc:
        raise ValueError(f"{field} must be numeric and finite") from exc
    if not result.is_finite():
        raise ValueError(f"{field} must be numeric and finite")
    return result


def _normal(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).strip().split()).casefold()


def _canonical_strings(values: tuple[str, ...]) -> tuple[str, ...]:
    unique: dict[str, str] = {}
    for value in values:
        display = " ".join(value.strip().split())
        key = _normal(display)
        if key and (key not in unique or display < unique[key]):
            unique[key] = display
    return tuple(unique[key] for key in sorted(unique))


@dataclass(frozen=True, slots=True)
class Term:
    term: str
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        primary = " ".join(self.term.strip().split())
        aliases = _canonical_strings(self.aliases)
        aliases = tuple(
            alias for alias in aliases if _normal(alias) != _normal(primary)
        )
        object.__setattr__(self, "term", primary)
        object.__setattr__(self, "aliases", aliases)


def _canonical_terms(values: tuple[Term, ...]) -> tuple[Term, ...]:
    grouped: dict[str, list[Term]] = {}
    for value in (Term(value.term, value.aliases) for value in values):
        key = _normal(value.term)
        if key:
            grouped.setdefault(key, []).append(value)
    output: list[Term] = []
    for key in sorted(grouped):
        items = grouped[key]
        primary = min(item.term for item in items)
        aliases = _canonical_strings(
            tuple(alias for item in items for alias in item.aliases)
        )
        output.append(Term(primary, aliases))
    primary_keys = {_normal(item.term) for item in output}
    alias_owners: dict[str, str] = {}
    for item in output:
        owner = _normal(item.term)
        for alias in item.aliases:
            alias_key = _normal(alias)
            if alias_key in primary_keys and alias_key != owner:
                raise ValueError("an alias cannot equal another primary term")
            previous = alias_owners.get(alias_key)
            if previous is not None and previous != owner:
                raise ValueError("an alias cannot belong to multiple primary terms")
            alias_owners[alias_key] = owner
    return tuple(output)


@dataclass(frozen=True, slots=True)
class BriefInput:
    required_skills: tuple[Term, ...] = ()
    optional_skills: tuple[Term, ...] = ()
    required_experience_months: int | None = None
    target_titles: tuple[Term, ...] = ()
    industries: tuple[Term, ...] = ()
    location: str = ""
    required_credentials: tuple[Term, ...] = ()
    positive_keywords: tuple[str, ...] = ()
    negative_keywords: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.required_experience_months is not None:
            if type(self.required_experience_months) is not int:
                raise ValueError("required experience months must be an integer")
            if self.required_experience_months < 0:
                raise ValueError("required experience months cannot be negative")
        for name in (
            "required_skills",
            "optional_skills",
            "target_titles",
            "industries",
            "required_credentials",
        ):
            object.__setattr__(self, name, _canonical_terms(getattr(self, name)))
        object.__setattr__(self, "location", " ".join(self.location.strip().split()))
        object.__setattr__(
            self, "positive_keywords", _canonical_strings(self.positive_keywords)
        )
        object.__setattr__(
            self, "negative_keywords", _canonical_strings(self.negative_keywords)
        )


@dataclass(frozen=True, slots=True)
class SignalWeight:
    signal_id: SignalId
    value: Decimal

    def __post_init__(self) -> None:
        try:
            signal_id = SignalId(self.signal_id)
        except ValueError as exc:
            raise ValueError(f"non-scorable signal id: {self.signal_id}") from exc
        value = _decimal_value(self.value, "signal weight")
        if value < 0:
            raise ValueError("signal weights must be finite and nonnegative")
        if value > MAX_SIGNAL_WEIGHT:
            raise ValueError("signal weight exceeds the supported maximum")
        object.__setattr__(self, "signal_id", signal_id)
        object.__setattr__(self, "value", value)


@dataclass(frozen=True, slots=True)
class MetroEquivalence:
    name: str
    locations: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", " ".join(self.name.strip().split()))
        object.__setattr__(self, "locations", _canonical_strings(self.locations))


DEFAULT_WEIGHTS = (
    SignalWeight(SignalId.REQUIRED_SKILLS, Decimal(30)),
    SignalWeight(SignalId.OPTIONAL_SKILLS, Decimal(10)),
    SignalWeight(SignalId.EXPERIENCE, Decimal(20)),
    SignalWeight(SignalId.TITLE, Decimal(15)),
    SignalWeight(SignalId.INDUSTRY, Decimal(10)),
    SignalWeight(SignalId.LOCATION, Decimal(8)),
    SignalWeight(SignalId.CREDENTIAL, Decimal(0)),
)


@dataclass(frozen=True, slots=True)
class ScoringConfigInput:
    weights: tuple[SignalWeight, ...] = DEFAULT_WEIGHTS
    metro_equivalences: tuple[MetroEquivalence, ...] = ()

    def __post_init__(self) -> None:
        weights = tuple(sorted(self.weights, key=lambda item: item.signal_id.value))
        ids = tuple(item.signal_id for item in weights)
        if len(set(ids)) != len(ids):
            raise ValueError("a signal weight may be configured only once")
        unknown = set(SIGNAL_ORDER).difference(ids)
        if unknown:
            labels = ", ".join(sorted(item.value for item in unknown))
            raise ValueError(f"missing signal weights: {labels}")
        metros = tuple(
            sorted(
                (
                    MetroEquivalence(item.name, item.locations)
                    for item in self.metro_equivalences
                ),
                key=lambda item: (_normal(item.name), item.locations),
            )
        )
        object.__setattr__(self, "weights", weights)
        object.__setattr__(self, "metro_equivalences", metros)

    def weight_for(self, signal_id: SignalId) -> Decimal:
        for item in self.weights:
            if item.signal_id is signal_id:
                return item.value
        raise AssertionError(f"validated weight is missing for {signal_id.value}")


@dataclass(frozen=True, slots=True)
class SearchContext:
    search_run_id: str
    relationship_filters: tuple[Literal["F", "S", "O"], ...]

    def __post_init__(self) -> None:
        filters = tuple(self.relationship_filters)
        if any(item not in {"F", "S", "O"} for item in filters):
            raise ValueError("unknown relationship filter")
        object.__setattr__(self, "relationship_filters", filters)


@dataclass(frozen=True, slots=True)
class ProfileSection:
    section_id: int | str
    name: str
    state: SectionState
    raw_text: str = ""
    content_sha256: str = ""
    missing_reason: MissingReason | None = None
    section_error_id: int | str | None = None

    def __post_init__(self) -> None:
        state = SectionState(self.state)
        missing_reason = (
            None if self.missing_reason is None else MissingReason(self.missing_reason)
        )
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "missing_reason", missing_reason)
        if state is SectionState.COMPLETE:
            if missing_reason is not None:
                raise ValueError("a completed section cannot have a missing reason")
            if not self.content_sha256:
                raise ValueError("a completed section requires a content hash")
        else:
            if self.raw_text or self.content_sha256:
                raise ValueError("a missing section cannot carry retrieved content")
            if missing_reason is None:
                raise ValueError("a missing section requires a reason")


@dataclass(frozen=True, slots=True)
class SourcedText:
    section_name: str
    section_id: int | str
    content_sha256: str
    text: str
    span: VerifiedSpan

    def __post_init__(self) -> None:
        if not self.text or self.text != self.span.snippet:
            raise ValueError("sourced text must equal its verified span")


@dataclass(frozen=True, slots=True)
class ExperienceRole:
    title: SourcedText
    description: SourcedText | None = None
    date_range: SourcedText | None = None
    duration: SourcedText | None = None
    months: int | None = None
    months_derivation: MonthsDerivation | None = None

    def __post_init__(self) -> None:
        sources = tuple(
            item
            for item in (
                self.title,
                self.description,
                self.date_range,
                self.duration,
            )
            if item is not None
        )
        if any(item.section_name != "experience" for item in sources):
            raise ValueError("role sources must come from the experience section")
        if self.months is None:
            if self.months_derivation is not None:
                raise ValueError("month derivation requires a numeric month value")
            return
        if type(self.months) is not int or self.months < 0:
            raise ValueError("role months must be a nonnegative integer")
        if self.months_derivation is None:
            raise ValueError("numeric role months require verified derivation metadata")
        derivation = MonthsDerivation(self.months_derivation)
        if derivation is MonthsDerivation.DATE_RANGE and self.date_range is None:
            raise ValueError("date-range derivation requires a verified date range")
        if derivation is MonthsDerivation.DURATION_TEXT and self.duration is None:
            raise ValueError("duration derivation requires verified duration text")
        object.__setattr__(self, "months_derivation", derivation)


@dataclass(frozen=True, slots=True)
class ProfileSnapshot:
    sections: tuple[ProfileSection, ...]
    titles: tuple[SourcedText, ...] = ()
    location: SourcedText | None = None
    experience_roles: tuple[ExperienceRole, ...] = ()

    def __post_init__(self) -> None:
        sections = tuple(sorted(self.sections, key=lambda item: item.name))
        if len({item.name for item in sections}) != len(sections):
            raise ValueError("a profile section may appear only once")
        object.__setattr__(self, "sections", sections)
        object.__setattr__(
            self,
            "titles",
            tuple(
                sorted(
                    self.titles,
                    key=lambda item: (
                        item.section_name,
                        item.span.start,
                        item.span.end,
                    ),
                )
            ),
        )
        object.__setattr__(
            self,
            "experience_roles",
            tuple(
                sorted(
                    self.experience_roles,
                    key=lambda item: (
                        item.title.section_name,
                        item.title.span.start,
                        item.title.span.end,
                    ),
                )
            ),
        )
        if any(
            item.section_name not in {"main_profile", "experience"}
            for item in self.titles
        ):
            raise ValueError("title sources must come from title-bearing sections")
        if self.location is not None and self.location.section_name != "main_profile":
            raise ValueError("location source must come from the main profile section")
        self._verify_sources()

    def section(self, name: str) -> ProfileSection | None:
        return next((item for item in self.sections if item.name == name), None)

    def _verify_sources(self) -> None:
        values = [*self.titles]
        if self.location is not None:
            values.append(self.location)
        for role in self.experience_roles:
            values.append(role.title)
            if role.description is not None:
                values.append(role.description)
            if role.date_range is not None:
                values.append(role.date_range)
            if role.duration is not None:
                values.append(role.duration)
        for value in values:
            section = self.section(value.section_name)
            if section is None or section.state is not SectionState.COMPLETE:
                raise ValueError("sourced text must reference a completed section")
            if (
                section.section_id != value.section_id
                or section.content_sha256 != value.content_sha256
                or section.raw_text[value.span.start : value.span.end]
                != value.span.snippet
            ):
                raise ValueError("sourced text does not resolve to section content")


@dataclass(frozen=True, slots=True)
class ProfileEvidence:
    matched_term: str
    matcher: Matcher
    section_name: str
    profile_section_id: int | str
    content_sha256: str
    span: VerifiedSpan
    polarity: Polarity = Polarity.SUPPORTING


@dataclass(frozen=True, slots=True)
class EvidenceSet:
    entries: tuple[ProfileEvidence, ...]

    def __post_init__(self) -> None:
        if not self.entries:
            raise ValueError("evidence sets cannot be empty")
        priority = {
            Matcher.EXACT: 0,
            Matcher.ALIAS: 1,
            Matcher.STEM: 2,
            Matcher.LLM_VERIFIED: 3,
        }
        unique: dict[
            tuple[str, int | str, str, int, int, Polarity], ProfileEvidence
        ] = {}
        for item in self.entries:
            key = (
                item.section_name,
                item.profile_section_id,
                item.content_sha256,
                item.span.start,
                item.span.end,
                item.polarity,
            )
            previous = unique.get(key)
            if previous is None or (
                priority[item.matcher],
                item.matched_term.casefold(),
            ) < (priority[previous.matcher], previous.matched_term.casefold()):
                unique[key] = item
        object.__setattr__(
            self,
            "entries",
            tuple(
                sorted(
                    unique.values(),
                    key=lambda item: (
                        item.section_name,
                        item.span.start,
                        item.span.end,
                        item.matcher.value,
                        item.polarity.value,
                    ),
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class AbsenceCoverage:
    profile_section_id: int | str
    section_name: str
    content_sha256: str
    normalized_terms: tuple[str, ...]
    aliases: tuple[str, ...]
    matcher_version: str


@dataclass(frozen=True, slots=True)
class CoverageSet:
    entries: tuple[AbsenceCoverage, ...]

    def __post_init__(self) -> None:
        if not self.entries:
            raise ValueError("coverage sets cannot be empty")
        object.__setattr__(
            self,
            "entries",
            tuple(sorted(self.entries, key=lambda item: item.section_name)),
        )


@dataclass(frozen=True, slots=True)
class MissingSection:
    section_name: str
    reason: MissingReason
    section_error_id: int | str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason", MissingReason(self.reason))


@dataclass(frozen=True, slots=True)
class MissingSet:
    entries: tuple[MissingSection, ...]

    def __post_init__(self) -> None:
        if not self.entries:
            raise ValueError("missing sets cannot be empty")
        object.__setattr__(
            self,
            "entries",
            tuple(
                sorted(
                    self.entries,
                    key=lambda item: (
                        item.section_name,
                        item.reason.value,
                        str(item.section_error_id or ""),
                    ),
                )
            ),
        )


type ClaimProvenance = EvidenceSet | CoverageSet | MissingSet


@dataclass(frozen=True, slots=True)
class ScoreClaim:
    claim_key: str
    display_term: str
    verdict: Verdict
    provenance: ClaimProvenance

    def __post_init__(self) -> None:
        verdict = Verdict(self.verdict)
        object.__setattr__(self, "verdict", verdict)
        expected: type[ClaimProvenance]
        if verdict in (Verdict.MATCHED, Verdict.CONTRADICTED):
            expected = EvidenceSet
        elif verdict is Verdict.NOT_MATCHED:
            expected = CoverageSet
        else:
            expected = MissingSet
        if not isinstance(self.provenance, expected):
            raise ValueError(f"{verdict.value} has incompatible provenance")
        if isinstance(self.provenance, EvidenceSet):
            polarities = {item.polarity for item in self.provenance.entries}
            if verdict is Verdict.MATCHED and polarities != {Polarity.SUPPORTING}:
                raise ValueError("matched evidence must be supporting")
            if verdict is Verdict.CONTRADICTED and polarities != {
                Polarity.CONTRADICTING
            }:
                raise ValueError(
                    "contradicted evidence must be exclusively contradicting"
                )


@dataclass(frozen=True, slots=True)
class ScoreSignal:
    signal_id: SignalId
    rollup: Rollup
    raw_subscore: Decimal
    availability: Decimal
    claims: tuple[ScoreClaim, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "signal_id", SignalId(self.signal_id))
        rollup = Rollup(self.rollup)
        subscore = _decimal_value(self.raw_subscore, "raw subscore")
        availability = _decimal_value(self.availability, "availability")
        if not (Decimal(0) <= subscore <= Decimal(1)):
            raise ValueError("raw subscore must be between zero and one")
        if not (Decimal(0) <= availability <= Decimal(1)):
            raise ValueError("availability must be between zero and one")
        if not self.claims:
            raise ValueError("active signals require at least one claim")
        claims = tuple(sorted(self.claims, key=lambda item: item.claim_key))
        if len({item.claim_key for item in claims}) != len(claims):
            raise ValueError("claim keys must be unique within a signal")
        verdicts = {item.verdict for item in claims}
        expected_rollup = (
            Rollup.MIXED if len(verdicts) != 1 else Rollup(next(iter(verdicts)).value)
        )
        if rollup is not expected_rollup:
            raise ValueError("signal rollup must agree with its claims")
        object.__setattr__(self, "rollup", rollup)
        object.__setattr__(self, "raw_subscore", subscore)
        object.__setattr__(self, "availability", availability)
        object.__setattr__(self, "claims", claims)


@dataclass(frozen=True, slots=True)
class PenaltyContribution:
    penalty_id: Literal["P-1", "P-2"]
    points: Decimal
    details: tuple[str, ...]
    evidence: tuple[ProfileEvidence, ...] = ()

    def __post_init__(self) -> None:
        if self.penalty_id not in {"P-1", "P-2"}:
            raise ValueError("unknown penalty id")
        points = _decimal_value(self.points, "penalty points")
        if points < 0:
            raise ValueError("penalty points must be finite and nonnegative")
        cap = Decimal(15) if self.penalty_id == "P-1" else Decimal(10)
        if points > cap:
            raise ValueError("penalty points exceed the defined cap")
        object.__setattr__(self, "points", points)


@dataclass(frozen=True, slots=True)
class ScoreCalculation:
    score: Decimal | None
    score_lower: Decimal | None
    score_upper: Decimal | None
    confidence: Decimal
    confidence_band: ConfidenceBand | None
    calculation_status: CalculationStatus
    active_signal_count: int
    stage: ScoreStage
    signals: tuple[ScoreSignal, ...]
    penalties: tuple[PenaltyContribution, ...]

    def __post_init__(self) -> None:
        stage = ScoreStage(self.stage)
        status = CalculationStatus(self.calculation_status)
        band = (
            None
            if self.confidence_band is None
            else ConfidenceBand(self.confidence_band)
        )
        confidence = _decimal_value(self.confidence, "confidence")
        signals = tuple(sorted(self.signals, key=lambda item: item.signal_id.value))
        penalties = tuple(sorted(self.penalties, key=lambda item: item.penalty_id))
        if self.active_signal_count != len(signals):
            raise ValueError("active signal count must equal emitted signals")
        if not (_ZERO_DECIMAL <= confidence <= Decimal(1)):
            raise ValueError("confidence must be between zero and one")
        values = (self.score, self.score_lower, self.score_upper)
        numeric = tuple(
            None if value is None else _decimal_value(value, "score")
            for value in values
        )
        if any(value is None for value in numeric) != all(
            value is None for value in numeric
        ):
            raise ValueError("score and bounds must be jointly nullable")
        if self.active_signal_count == 0:
            if (
                any(value is not None for value in numeric)
                or confidence != 0
                or band is not ConfidenceBand.LOW
                or status is not CalculationStatus.UNKNOWN
                or penalties
            ):
                raise ValueError("all-inert calculation has invalid fields")
        elif numeric[0] is None:
            if (
                confidence != 0
                or band is not None
                or status is not CalculationStatus.UNKNOWN
            ):
                raise ValueError("unavailable calculation has invalid fields")
        else:
            score, lower, upper = numeric
            if score is None or lower is None or upper is None:
                raise AssertionError("joint nullability was already validated")
            if not (_ZERO_DECIMAL <= lower <= score <= upper <= Decimal(100)):
                raise ValueError("numeric score bounds are invalid")
            if band is None or status is not CalculationStatus.SCORED:
                raise ValueError(
                    "numeric calculation requires a band and scored status"
                )
        object.__setattr__(self, "score", numeric[0])
        object.__setattr__(self, "score_lower", numeric[1])
        object.__setattr__(self, "score_upper", numeric[2])
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "confidence_band", band)
        object.__setattr__(self, "calculation_status", status)
        object.__setattr__(self, "stage", stage)
        object.__setattr__(self, "signals", signals)
        object.__setattr__(self, "penalties", penalties)
