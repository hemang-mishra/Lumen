"""
The shapes a periodic report passes through on its way to being written.

There are four of them, in order, and the boundaries between them are the
whole design of this package.

`WindowCorpus` is everything read out of the stores for one stretch of time,
and nothing after it reads anything. `ComputedFacts` is every number in the
report and holds no prose at all. `NarrativeDraft` is every sentence in the
report and holds no numbers at all. `ReportOutcome` is what happened.

Keeping the third apart from the second is the point. A model is very good at
writing "your reference frame has been shifting toward internal standards"
and quite capable of writing "this fired six times" about something that
fired twice. Counting happens in code, phrasing happens in the model, and the
two never touch the same field.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lumen.schemas.enums import (
    ArcDirection,
    GapStatus,
    MacroRunStatus,
    NarrativeStatus,
    PatternAgeBand,
    PatternTrend,
    ReportType,
)


class MacroWindow(BaseModel):
    """
    One stretch of time a report covers.

    The end is not included. Two periods that ran back to back would
    otherwise share whatever sat exactly on the boundary, and the same
    episode counted in two consecutive months makes both months wrong.

    Attributes:
        report_type: Which kind of report this window belongs to.
        period_start: The first moment the report covers.
        period_end: The first moment it no longer covers.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    report_type: ReportType
    period_start: datetime
    period_end: datetime

    @model_validator(mode="after")
    def _validate_ordering(self) -> "MacroWindow":
        """A window must move forwards."""
        if self.period_end <= self.period_start:
            raise ValueError("period_end must come after period_start")
        return self

    @property
    def start_date(self) -> date:
        """The first day covered."""
        return self.period_start.date()

    @property
    def end_date(self) -> date:
        """The first day no longer covered."""
        return self.period_end.date()

    @property
    def key(self) -> tuple[str, str]:
        """
        What identifies this window among reports already written.

        The kind and the start together, because a week and a month can begin
        on the same morning and are not the same period.
        """
        return (self.report_type.value, self.period_start.isoformat())


class ObservationFacts(BaseModel):
    """
    One thing noticed, reduced to what a report needs from it.

    Attributes:
        node_id: What it is called.
        type: Which kind of noticing it was.
        content: What it says.
        signal_strength: How weighty it was judged to be.
        person_refs: Anyone it named.
        episode_id: The writing it came out of.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str = Field(min_length=1)
    type: str = Field(min_length=1)
    content: str = ""
    signal_strength: str = ""
    person_refs: tuple[str, ...] = ()
    episode_id: str = Field(min_length=1)


class EpisodeFacts(BaseModel):
    """
    One piece of writing in the window, with what it produced.

    Attributes:
        episode_id: What it is called.
        event_date: The day it is about, which is what puts it in this window.
        occurred_at: The moment within that day.
        episode_summary: What it was about, in a line.
        historical_era: The named period of the past it reaches back to, if any.
        observations: Everything noticed in it.
        finding_ids: Every record it produced that a standing record can hang
            off — the observations, plus the events and sessions.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    episode_id: str = Field(min_length=1)
    event_date: date
    occurred_at: datetime
    episode_summary: str = ""
    historical_era: str | None = None
    observations: tuple[ObservationFacts, ...] = ()
    finding_ids: tuple[str, ...] = ()


class StandingLink(BaseModel):
    """
    One link from something noticed to something the person carries.

    A pattern is never recorded as having "fired in an episode" anywhere.
    What exists is a link from a finding to the pattern, and the finding
    knows its episode. Following that pair is how every count in a report is
    arrived at.

    Attributes:
        from_id: The finding.
        to_id: The standing record it points at.
        to_type: Which kind of standing record that is.
        edge_name: What the link means.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    from_id: str = Field(min_length=1)
    to_id: str = Field(min_length=1)
    to_type: str = ""
    edge_name: str = Field(min_length=1)


class WindowCorpus(BaseModel):
    """
    Everything one report was able to read, and nothing else.

    This is the boundary between the part of the package that touches a store
    and the part that does not. Every calculation downstream works on this
    object alone, which is what lets the whole of the arithmetic be tested
    without a database.

    Attributes:
        window: The stretch of time being reported on.
        episodes: The writing in it, oldest first.
        links: What that writing turned into.
        patterns: The pattern records those links point at, by id.
        beliefs: The belief records those links point at, by id.
        people: The people named, by id.
        lessons: Lessons from this window and from far enough before it to
            tell which ones have gone quiet.
        contradictions: Tensions recorded in the window.
        open_loops: Questions the person is still working through.
        decisions: Notes of what reconciliation decided during the window.
        all_patterns: Every live pattern, whether or not it fired here. The
            ageing section is deliberately not limited to the window — a
            pattern is ageing precisely because it is absent.
        previous_pattern_frequency: How often each pattern fired in the
            period before this one, which is the only way to see that
            something stopped.
        previous_episode_count: How much writing that earlier period held.
        comparison_counts: Episode counts per pattern over the longer
            stretch used to spot an identity-level shift.
        awareness_counts: How often each pattern was caught in the act, this
            window and the one before.
        pending_review: How many decisions are waiting for the person, and
            when the oldest was raised.
        truncated: True when a cap stopped the reading short.
    """

    model_config = ConfigDict(extra="forbid")

    window: MacroWindow
    episodes: list[EpisodeFacts] = Field(default_factory=list)
    links: list[StandingLink] = Field(default_factory=list)
    patterns: dict[str, dict[str, Any]] = Field(default_factory=dict)
    beliefs: dict[str, dict[str, Any]] = Field(default_factory=dict)
    people: dict[str, dict[str, Any]] = Field(default_factory=dict)
    lessons: list[dict[str, Any]] = Field(default_factory=list)
    contradictions: list[dict[str, Any]] = Field(default_factory=list)
    open_loops: list[dict[str, Any]] = Field(default_factory=list)
    decisions: list[dict[str, Any]] = Field(default_factory=list)
    all_patterns: list[dict[str, Any]] = Field(default_factory=list)
    previous_pattern_frequency: dict[str, float] = Field(default_factory=dict)
    previous_pattern_episodes: dict[str, int] = Field(default_factory=dict)
    previous_episode_count: int = Field(default=0, ge=0)
    comparison_counts: dict[str, int] = Field(default_factory=dict)
    awareness_counts: dict[str, int] = Field(default_factory=dict)
    previous_awareness_counts: dict[str, int] = Field(default_factory=dict)
    closed_loop_ids: tuple[str, ...] = ()
    pending_review: tuple[int, datetime | None] = (0, None)
    truncated: bool = False

    @property
    def episode_ids(self) -> tuple[str, ...]:
        """Every piece of writing in the window, in order."""
        return tuple(episode.episode_id for episode in self.episodes)

    @property
    def is_empty(self) -> bool:
        """True when nothing was written about this stretch of time."""
        return not self.episodes


# ---------------------------------------------------------------------------
# The arithmetic — every field below is counted, none is written
# ---------------------------------------------------------------------------


class PatternCount(BaseModel):
    """
    How often one pattern showed up, and when.

    Attributes:
        pattern_id: Which pattern.
        label: What it is called, for a reader.
        episode_count: How many separate pieces of writing it appeared in.
        frequency_pct: That count as a share of the window, so windows of
            different lengths can be compared at all.
        first_seen: The earliest day in the window it appeared.
        last_seen: The latest.
    """

    model_config = ConfigDict(extra="forbid")

    pattern_id: str = Field(min_length=1)
    label: str = ""
    episode_count: int = Field(ge=0)
    frequency_pct: float = Field(ge=0.0)
    first_seen: date | None = None
    last_seen: date | None = None


class EmergingPattern(BaseModel):
    """A pattern seen here for the first time."""

    model_config = ConfigDict(extra="forbid")

    pattern_id: str = Field(min_length=1)
    label: str = ""
    first_episode: str = ""
    first_seen: date | None = None


class DisappearingPattern(BaseModel):
    """
    A pattern that was firing before and did not fire at all here.

    Not the same as resolved, and the report does not claim it is. It is the
    shape resolution takes in the record, and it is also the shape of a month
    somebody was too busy to write about.
    """

    model_config = ConfigDict(extra="forbid")

    pattern_id: str = Field(min_length=1)
    label: str = ""
    previous_frequency_pct: float = Field(ge=0.0)
    last_reinforced: datetime | None = None


class BeliefChange(BaseModel):
    """One belief that took a new shape during the window."""

    model_config = ConfigDict(extra="forbid")

    belief_id: str = Field(min_length=1)
    old_version: int | None = None
    old_content: str = ""
    new_version: int | None = None
    new_content: str = ""
    delta_description: str | None = None
    evolved_on: datetime | None = None


class RepeatedLesson(BaseModel):
    """A lesson the window arrived at more than once."""

    model_config = ConfigDict(extra="forbid")

    lesson_id: str = Field(min_length=1)
    content: str = ""
    appearance_count: int = Field(ge=0)


class IgnoredLesson(BaseModel):
    """A lesson learned earlier and not touched since."""

    model_config = ConfigDict(extra="forbid")

    lesson_id: str = Field(min_length=1)
    content: str = ""
    last_seen: date | None = None
    days_since_last_seen: int = Field(ge=0)


class GrowthCandidate(BaseModel):
    """
    The pattern the window has the best case for calling progress.

    Chosen by rule — the largest drop in how often something fired, among
    those with some record of having been worked on. The sentence explaining
    it comes from the model afterwards.
    """

    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1)
    name: str = ""
    episode_count: int = Field(ge=0)
    previous_episode_count: int = Field(ge=0)
    was_evolved: bool = False
    was_regulated: bool = False


class StruggleCandidate(BaseModel):
    """
    The pattern that took up the most of the window.

    `negative_observation_count` counts the difficult-feeling observations
    that appeared alongside it. It is a count of things that were written,
    not a score of how bad the window felt — nothing in Lumen measures that,
    and a number invented for the purpose would read exactly like one that
    had been measured.
    """

    model_config = ConfigDict(extra="forbid")

    pattern_id: str = Field(min_length=1)
    name: str = ""
    episode_count: int = Field(ge=0)
    negative_observation_count: int = Field(ge=0)


class RelationalDynamic(BaseModel):
    """How much of the window involved one particular person."""

    model_config = ConfigDict(extra="forbid")

    person_ref: str = Field(min_length=1)
    person_id: str | None = None
    observation_count: int = Field(ge=0)
    excerpts: tuple[str, ...] = ()


class EnvironmentObservation(BaseModel):
    """One noticing about a place the person depends on."""

    model_config = ConfigDict(extra="forbid")

    observation_id: str = Field(min_length=1)
    content: str = ""
    signal_strength: str = ""


class OpenLoopFacts(BaseModel):
    """A question still open at the end of the window."""

    model_config = ConfigDict(extra="forbid")

    open_loop_id: str = Field(min_length=1)
    content: str = ""
    first_raised: datetime | None = None
    days_open: int = Field(ge=0)


class HighSignalObservation(BaseModel):
    """One noticing weighty enough to be carried whatever else the window held."""

    model_config = ConfigDict(extra="forbid")

    observation_id: str = Field(min_length=1)
    type: str = ""
    signal_strength: str = ""
    content: str = ""
    episode_id: str = ""


class UnprocessedMotif(BaseModel):
    """A pattern reached only through feelings that surfaced unbidden."""

    model_config = ConfigDict(extra="forbid")

    pattern_id: str = Field(min_length=1)
    label: str = ""
    suppressed_surfacing_count: int = Field(ge=0)


class RelationshipArcFacts(BaseModel):
    """What can be counted about one relationship across the window."""

    model_config = ConfigDict(extra="forbid")

    person_id: str = Field(min_length=1)
    canonical_name: str = ""
    episodes_in_window: int = Field(ge=0)
    dominant_observation_types: tuple[str, ...] = ()
    stored_direction: ArcDirection | None = None
    excerpts: tuple[str, ...] = ()


class BiographicalGapFacts(BaseModel):
    """One noticing that something is missing from the person's history."""

    model_config = ConfigDict(extra="forbid")

    observation_id: str = Field(min_length=1)
    content: str = ""
    first_raised: datetime | None = None


class AgingPattern(BaseModel):
    """A pattern that has gone quiet for long enough to be worth less."""

    model_config = ConfigDict(extra="forbid")

    pattern_id: str = Field(min_length=1)
    label: str = ""
    band: PatternAgeBand
    last_reinforced: datetime | None = None
    days_since_last_seen: int = Field(ge=0)
    weight_multiplier: float = Field(gt=0.0)
    re_interrogation_prompt: str | None = None


class ProofInstance(BaseModel):
    """One occasion behind a long-running pattern."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    episode_id: str = Field(min_length=1)
    happened_at: datetime
    excerpt: str = ""


class ProofChain(BaseModel):
    """
    The evidence that one thing about a person has been true for years.

    Kept apart from the ageing report on purpose. That one says a pattern has
    gone quiet; this one says a pattern has kept coming back, and the two are
    opposite findings that happen to be about the same kind of record.

    Attributes:
        record_id: The pattern or lesson being proved.
        record_type: Which of the two it is.
        label: What it says, shortened.
        total_instances: How many separate occasions there have been.
        span_days: How far back the earliest reaches from the latest.
        first_seen: The earliest occasion.
        last_seen: The most recent.
        key_instances: A handful spread across the span, oldest first.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    record_id: str = Field(min_length=1)
    record_type: str = ""
    label: str = ""
    total_instances: int = Field(ge=0)
    span_days: int = Field(ge=0)
    first_seen: datetime
    last_seen: datetime
    key_instances: list[ProofInstance] = Field(default_factory=list)

    @property
    def span_years(self) -> float:
        """The span in years, to one decimal place."""
        return round(self.span_days / 365.25, 1)

    @property
    def summary(self) -> str:
        """
        The chain in one sentence, built from the counts rather than written.

        Deliberately not a model's work. It is the same sentence every time
        with two numbers changed, and a model given the job would word it
        differently in every report while adding nothing — and could reach
        for detail the arithmetic never established.
        """
        occasions = f"{self.total_instances} separate occasions"
        if self.span_years >= 1.0:
            return f"This has come back on {occasions} over {self.span_years} years."
        return f"This has come back on {occasions} over {self.span_days} days."


class TrendingPattern(BaseModel):
    """One pattern's direction of travel between two stretches of time."""

    model_config = ConfigDict(extra="forbid")

    pattern_id: str = Field(min_length=1)
    label: str = ""
    trend: PatternTrend
    recent_count: int = Field(ge=0)
    earlier_count: int = Field(ge=0)


class ArchetypeShiftFacts(BaseModel):
    """
    Whether enough separate patterns moved the same way to call it a shift.

    Detection is arithmetic: count the patterns trending in a consistent
    direction and compare against a threshold. What the shift *is* — the name
    for it, the sentence describing it — is the model's, and only when this
    says it happened.
    """

    model_config = ConfigDict(extra="forbid")

    detected: bool = False
    contributing_patterns: tuple[TrendingPattern, ...] = ()
    comparison_start: datetime | None = None
    comparison_end: datetime | None = None


class ContradictionFacts(BaseModel):
    """
    One tension recorded during the window and not yet resolved.

    Both beliefs are carried as the person's own words where those could be
    found, because a tension stated as two identifiers is not something
    anybody can reflect on.
    """

    model_config = ConfigDict(extra="forbid")

    contradiction_id: str = Field(min_length=1)
    belief_a: str = ""
    belief_b: str = ""
    summary: str = ""
    first_detected: datetime | None = None
    days_open: int = Field(ge=0)


class ComputedFacts(BaseModel):
    """
    Every number in one report.

    Assembled entirely from `WindowCorpus` by pure functions. Nothing here
    needed a model, which means every figure in a finished report can be
    checked by hand against the graph it came from.
    """

    model_config = ConfigDict(extra="forbid")

    window: MacroWindow
    episodes_analyzed: int = Field(ge=0)
    truncated: bool = False

    # Everything the report read, including the pieces of writing that
    # produced nothing worth a line. These become the report's coverage
    # links, and a report that claims a period without naming what it
    # actually looked at is an assertion rather than a record.
    episode_ids: list[str] = Field(default_factory=list)

    top_patterns: list[PatternCount] = Field(default_factory=list)
    pattern_frequency: list[PatternCount] = Field(default_factory=list)
    emerging_patterns: list[EmergingPattern] = Field(default_factory=list)
    disappearing_patterns: list[DisappearingPattern] = Field(default_factory=list)

    belief_changes: list[BeliefChange] = Field(default_factory=list)
    repeated_lessons: list[RepeatedLesson] = Field(default_factory=list)
    ignored_lessons: list[IgnoredLesson] = Field(default_factory=list)

    growth_candidate: GrowthCandidate | None = None
    struggle_candidate: StruggleCandidate | None = None

    relational_dynamics: list[RelationalDynamic] = Field(default_factory=list)
    environment_observations: list[EnvironmentObservation] = Field(default_factory=list)

    unresolved_open_loops: list[OpenLoopFacts] = Field(default_factory=list)
    pending_review_count: int = Field(default=0, ge=0)
    pending_review_oldest_days: int | None = None

    high_signal_observations: list[HighSignalObservation] = Field(default_factory=list)
    unprocessed_motifs: list[UnprocessedMotif] = Field(default_factory=list)
    relationship_arcs: list[RelationshipArcFacts] = Field(default_factory=list)
    biographical_gaps: list[BiographicalGapFacts] = Field(default_factory=list)
    pattern_aging: list[AgingPattern] = Field(default_factory=list)
    proof_chains: list[ProofChain] = Field(default_factory=list)
    archetype_shift: ArchetypeShiftFacts = Field(default_factory=ArchetypeShiftFacts)
    active_contradictions: list[ContradictionFacts] = Field(default_factory=list)

    @property
    def known_pattern_ids(self) -> frozenset[str]:
        """Every pattern this report has something to say about."""
        return frozenset(item.pattern_id for item in self.pattern_frequency) | frozenset(
            item.pattern_id for item in self.pattern_aging
        )


# ---------------------------------------------------------------------------
# The prose — every field below is written, none is counted
# ---------------------------------------------------------------------------


class RelationalSummary(BaseModel):
    """A sentence about how things stand with one person."""

    model_config = ConfigDict(extra="ignore")

    person_ref: str = ""
    dynamic_summary: str = ""


class EnvironmentGroup(BaseModel):
    """
    Several noticings about the same place, gathered under one heading.

    The grouping is the model's, because "the office" and "my desk at work"
    are the same place and no rule in code will ever say so. The confidence
    attached to the group is not the model's — it is worked out from how
    weighty the noticings underneath it were.
    """

    model_config = ConfigDict(extra="ignore")

    environment: str = ""
    dependency: str = ""
    observation_ids: list[str] = Field(default_factory=list)


class ArcNarrative(BaseModel):
    """A sentence about where one relationship has travelled."""

    model_config = ConfigDict(extra="ignore")

    person_id: str = ""
    arc_summary: str = ""
    arc_direction: ArcDirection | None = None


class GapJudgement(BaseModel):
    """Where one missing piece of the story now stands, and why."""

    model_config = ConfigDict(extra="ignore")

    observation_id: str = ""
    status: GapStatus = GapStatus.PRESENT
    closing_evidence: str | None = None


class ContradictionPrompt(BaseModel):
    """A question to put to the person about a tension they are holding."""

    model_config = ConfigDict(extra="ignore")

    contradiction_id: str = ""
    reflection_prompt: str = ""


class ArchetypeNarrative(BaseModel):
    """What to call a shift, once the arithmetic has said there is one."""

    model_config = ConfigDict(extra="ignore")

    shift_label: str = ""
    evidence_summary: str = ""


class NarrativeDraft(BaseModel):
    """
    Everything the model is asked to write for one report.

    Extra keys are ignored rather than rejected. A model that adds a field
    nobody asked for has still answered the question, and throwing the whole
    reply away over it would cost the report all of its prose.
    """

    model_config = ConfigDict(extra="ignore")

    headline: str = ""
    growth_area_label: str | None = None
    growth_area_evidence: str | None = None
    struggle_label: str | None = None
    relational_summaries: list[RelationalSummary] = Field(default_factory=list)
    environment_groups: list[EnvironmentGroup] = Field(default_factory=list)
    relationship_arcs: list[ArcNarrative] = Field(default_factory=list)
    biographical_gaps: list[GapJudgement] = Field(default_factory=list)
    contradiction_prompts: list[ContradictionPrompt] = Field(default_factory=list)
    archetype_shift: ArchetypeNarrative | None = None


class NarrativeResult(BaseModel):
    """
    The prose, plus how much of it could be trusted.

    The two travel together because the draft alone cannot say whether it is
    complete. A report whose model invented three node identifiers and a
    report whose model answered perfectly hold the same shape afterwards; the
    difference is recorded here and carried onto the report itself.
    """

    model_config = ConfigDict(extra="forbid")

    draft: NarrativeDraft = Field(default_factory=NarrativeDraft)
    status: NarrativeStatus = NarrativeStatus.UNAVAILABLE
    model_used: str = ""
    dropped_references: int = Field(default=0, ge=0)
    reason: str | None = None


class ShadowFinding(BaseModel):
    """
    What the two-day scan saw.

    Attributes:
        detected: Whether there was a burst at all.
        trigger_nodes: The decisions that made it up.
        episode_ids: The writing behind those decisions.
        branch_count: How many of them were something new branching off.
        contradict_count: How many were a tension being recorded.
        target_count: How many separate things were affected, which is what
            separates a burst from one thing being worked through repeatedly.
    """

    model_config = ConfigDict(extra="forbid")

    detected: bool = False
    trigger_nodes: tuple[str, ...] = ()
    episode_ids: tuple[str, ...] = ()
    branch_count: int = Field(default=0, ge=0)
    contradict_count: int = Field(default=0, ge=0)
    target_count: int = Field(default=0, ge=0)


class ShadowNarrative(BaseModel):
    """The one or two sentences describing a burst."""

    model_config = ConfigDict(extra="ignore")

    shift_type: str = ""
    summary: str = ""


class ReportOutcome(BaseModel):
    """
    What one attempt at a report came to.

    Every field is filled on every path, including the paths where nothing
    was written. A scheduler needs to tell "there was nothing to report" from
    "the report failed", and a caller that only ever saw an identifier or a
    None could not.
    """

    model_config = ConfigDict(extra="forbid")

    status: MacroRunStatus
    window: MacroWindow
    report_id: str | None = None
    episodes_analyzed: int = Field(default=0, ge=0)
    narrative_status: NarrativeStatus = NarrativeStatus.UNAVAILABLE
    duration_ms: int = Field(default=0, ge=0)
    error: str | None = None

    @property
    def wrote_something(self) -> bool:
        """True when this attempt left a report behind."""
        return self.status is MacroRunStatus.WRITTEN


__all__ = [
    "MacroWindow",
    "ObservationFacts",
    "EpisodeFacts",
    "StandingLink",
    "WindowCorpus",
    "PatternCount",
    "EmergingPattern",
    "DisappearingPattern",
    "BeliefChange",
    "RepeatedLesson",
    "IgnoredLesson",
    "GrowthCandidate",
    "StruggleCandidate",
    "RelationalDynamic",
    "EnvironmentObservation",
    "OpenLoopFacts",
    "HighSignalObservation",
    "UnprocessedMotif",
    "RelationshipArcFacts",
    "BiographicalGapFacts",
    "AgingPattern",
    "TrendingPattern",
    "ArchetypeShiftFacts",
    "ContradictionFacts",
    "ComputedFacts",
    "RelationalSummary",
    "EnvironmentGroup",
    "ArcNarrative",
    "GapJudgement",
    "ContradictionPrompt",
    "ArchetypeNarrative",
    "NarrativeDraft",
    "NarrativeResult",
    "ShadowFinding",
    "ShadowNarrative",
    "ReportOutcome",
]
