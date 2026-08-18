"""
Counting what a stretch of somebody's history actually contained.

Every number that ends up in a report is worked out here, from the corpus
alone, with no model and no database. That is deliberate and it is the main
architectural claim of this package: a figure in a finished report can be
checked by hand against the graph it came from, and two runs over the same
month will always agree about how many times something happened.

The functions are separate rather than one long pass because each answers a
different question and each is worth being wrong about independently. They
share only the small index built at the top, which maps every pattern and
belief to the pieces of writing it appeared in — the one derived fact almost
every section needs.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from typing import Any

from lumen.config import MacroConfig
from lumen.pipeline.macroextraction.contracts import (
    BeliefChange,
    BiographicalGapFacts,
    ComputedFacts,
    ContradictionFacts,
    DisappearingPattern,
    EmergingPattern,
    EnvironmentObservation,
    GrowthCandidate,
    HighSignalObservation,
    IgnoredLesson,
    OpenLoopFacts,
    PatternCount,
    RelationalDynamic,
    RelationshipArcFacts,
    RepeatedLesson,
    StruggleCandidate,
    UnprocessedMotif,
    WindowCorpus,
)
from lumen.pipeline.macroextraction.aging import age_patterns
from lumen.pipeline.macroextraction.shifts import detect_shift
from lumen.schemas.enums import (
    ArcDirection,
    ObservationType,
    ReconciliationAction,
    SentimentTrend,
    SignalStrength,
)

logger = logging.getLogger(__name__)

# The kinds of noticing that record something being hard. Used only to say
# how much difficulty accompanied a pattern — a count of what was written,
# never a measure of how the period felt. Nothing in Lumen measures that.
NEGATIVE_AFFECT_TYPES: frozenset[str] = frozenset(
    {
        ObservationType.EMOTION.value,
        ObservationType.SOMATIC_STATE.value,
        ObservationType.ANTICIPATORY_ANXIETY.value,
        ObservationType.COGNITIVE_FRICTION.value,
        ObservationType.RUMINATION_LOOP.value,
        ObservationType.SUPPRESSED_EMOTION_SURFACING.value,
        ObservationType.CORE_WOUND.value,
        ObservationType.INAUTHENTICITY_STATE.value,
        ObservationType.SOCIAL_PERFORMANCE_STATE.value,
        ObservationType.COGNITIVE_DISTORTION_STATE.value,
    }
)

# Noticings that are always carried into a report whatever else it holds.
WEIGHTY_SIGNALS: frozenset[str] = frozenset(
    {SignalStrength.HIGH.value, SignalStrength.CRITICAL.value}
)

# How a person's recorded feeling about a relationship reads as a direction
# of travel. Only the unambiguous ones map; the rest are left for the model,
# which has the actual sentences in front of it.
_TREND_DIRECTIONS: dict[str, ArcDirection] = {
    SentimentTrend.POSITIVE.value: ArcDirection.STRENGTHENING,
    SentimentTrend.NEUTRAL.value: ArcDirection.STABLE,
    SentimentTrend.NEUTRAL_TO_NEGATIVE.value: ArcDirection.STRAINING,
    SentimentTrend.NEGATIVE.value: ArcDirection.STRAINING,
}

# The links that mean somebody deliberately worked on a pattern rather than
# merely repeated it.
_REGULATION_EDGES: frozenset[str] = frozenset({"regulates_obs", "regulates_sess"})


class WindowIndex:
    """
    The one derived fact nearly every section of a report needs.

    Built once and passed around: which pieces of writing each pattern and
    belief appeared in, and which noticings sat inside each of those pieces.
    Without it every section would walk the same links again, and the report
    would be ten passes over the same list rather than ten questions asked of
    one index.
    """

    def __init__(self, corpus: WindowCorpus) -> None:
        self.corpus = corpus
        self.total_episodes = len(corpus.episodes)

        self.episode_of: dict[str, str] = {
            finding_id: episode.episode_id
            for episode in corpus.episodes
            for finding_id in episode.finding_ids
        }
        self.date_of: dict[str, date] = {
            episode.episode_id: episode.event_date for episode in corpus.episodes
        }

        self.pattern_episodes: dict[str, set[str]] = defaultdict(set)
        self.belief_episodes: dict[str, set[str]] = defaultdict(set)
        self.person_episodes: dict[str, set[str]] = defaultdict(set)
        self.regulated: set[str] = set()

        for link in corpus.links:
            episode_id = self.episode_of.get(link.from_id)
            if episode_id is None:
                continue
            if link.to_type == "pattern":
                self.pattern_episodes[link.to_id].add(episode_id)
                if link.edge_name in _REGULATION_EDGES:
                    self.regulated.add(link.to_id)
            elif link.to_type == "belief":
                self.belief_episodes[link.to_id].add(episode_id)
            elif link.to_type == "person":
                self.person_episodes[link.to_id].add(episode_id)

        self.observations_of: dict[str, list] = {
            episode.episode_id: list(episode.observations)
            for episode in corpus.episodes
        }

    def label_for(self, pattern_id: str) -> str:
        """What a pattern is called, or its identifier if the record is gone."""
        record = self.corpus.patterns.get(pattern_id, {})
        return str(record.get("pattern_name") or pattern_id)

    def share(self, count: int) -> float:
        """A count as a percentage of the window, rounded to one place."""
        if self.total_episodes <= 0:
            return 0.0
        return round(count / self.total_episodes * 100, 1)

    def days_between(self, earlier: datetime | None, later: datetime) -> int:
        """Whole days from one moment to another, never negative."""
        if earlier is None:
            return 0
        return max((_utc(later) - _utc(earlier)).days, 0)


def compute(corpus: WindowCorpus, *, config: MacroConfig) -> ComputedFacts:
    """
    Every number in one report, from one reading of the graph.

    An empty window is answered honestly rather than refused — the caller
    decides that a period with nothing in it deserves no report, and this
    reports that there is nothing rather than making that decision itself.
    """
    index = WindowIndex(corpus)
    frequency = pattern_frequency(index)

    return ComputedFacts(
        window=corpus.window,
        episodes_analyzed=index.total_episodes,
        truncated=corpus.truncated,
        episode_ids=list(corpus.episode_ids),
        top_patterns=frequency[: max(config.top_patterns_limit, 1)],
        pattern_frequency=frequency,
        emerging_patterns=emerging_patterns(index),
        disappearing_patterns=disappearing_patterns(index),
        belief_changes=belief_changes(index),
        repeated_lessons=repeated_lessons(index, config=config),
        ignored_lessons=ignored_lessons(index, config=config),
        growth_candidate=growth_candidate(index),
        struggle_candidate=struggle_candidate(index, frequency),
        relational_dynamics=relational_dynamics(index, config=config),
        environment_observations=environment_observations(index),
        unresolved_open_loops=unresolved_open_loops(index, config=config),
        pending_review_count=corpus.pending_review[0],
        pending_review_oldest_days=_oldest_pending_days(corpus),
        high_signal_observations=high_signal_observations(index, config=config),
        unprocessed_motifs=unprocessed_motifs(index),
        relationship_arcs=relationship_arcs(index, config=config),
        biographical_gaps=biographical_gaps(index),
        pattern_aging=age_patterns(corpus, config=config),
        archetype_shift=detect_shift(corpus, index.pattern_episodes, config=config),
        active_contradictions=active_contradictions(index),
    )


def pattern_frequency(index: WindowIndex) -> list[PatternCount]:
    """
    How often each pattern appeared, most often first.

    Counted in pieces of writing rather than in noticings. A single entry
    that circles the same thing four times is one occasion of it happening,
    and counting the mentions would make a talkative day look like a month.
    """
    counts: list[PatternCount] = []

    for pattern_id, episode_ids in index.pattern_episodes.items():
        days = sorted(
            index.date_of[episode_id]
            for episode_id in episode_ids
            if episode_id in index.date_of
        )
        counts.append(
            PatternCount(
                pattern_id=pattern_id,
                label=index.label_for(pattern_id),
                episode_count=len(episode_ids),
                frequency_pct=index.share(len(episode_ids)),
                first_seen=days[0] if days else None,
                last_seen=days[-1] if days else None,
            )
        )

    counts.sort(key=lambda item: (-item.episode_count, item.pattern_id))
    return counts


def emerging_patterns(index: WindowIndex) -> list[EmergingPattern]:
    """
    Patterns showing up for the first time.

    Judged by the pattern being a first version that began inside the window,
    rather than by it being absent from the previous one. A pattern can be
    absent from a month for no better reason than a quiet month; a first
    version dated inside the window really is new.
    """
    window = index.corpus.window
    found: list[EmergingPattern] = []

    for pattern_id, episode_ids in index.pattern_episodes.items():
        record = index.corpus.patterns.get(pattern_id)
        if record is None:
            continue
        if int(record.get("version") or 1) != 1:
            continue

        began = _moment(record.get("valid_from") or record.get("created_at"))
        if began is None or not (
            window.period_start <= _utc(began) < window.period_end
        ):
            continue

        earliest = min(
            (episode_id for episode_id in episode_ids if episode_id in index.date_of),
            key=lambda episode_id: index.date_of[episode_id],
            default="",
        )
        found.append(
            EmergingPattern(
                pattern_id=pattern_id,
                label=index.label_for(pattern_id),
                first_episode=earliest,
                first_seen=index.date_of.get(earliest),
            )
        )

    found.sort(key=lambda item: (item.first_seen or date.min, item.pattern_id))
    return found


def disappearing_patterns(index: WindowIndex) -> list[DisappearingPattern]:
    """
    Patterns that were firing before and did not fire at all here.

    Reported without a claim about why. Something that has stopped showing up
    may have resolved, or may simply not have come up — the record cannot
    tell those apart, and neither should the report.
    """
    corpus = index.corpus
    by_id = {str(row.get("node_id")): row for row in corpus.all_patterns}
    gone: list[DisappearingPattern] = []

    for pattern_id, share in corpus.previous_pattern_frequency.items():
        if pattern_id in index.pattern_episodes or share <= 0:
            continue
        record = by_id.get(pattern_id) or corpus.patterns.get(pattern_id)
        if record is None:
            continue
        gone.append(
            DisappearingPattern(
                pattern_id=pattern_id,
                label=str(record.get("pattern_name") or pattern_id),
                previous_frequency_pct=share,
                last_reinforced=_moment(record.get("last_reinforced_at")),
            )
        )

    gone.sort(key=lambda item: (-item.previous_frequency_pct, item.pattern_id))
    return gone


def belief_changes(index: WindowIndex) -> list[BeliefChange]:
    """
    Beliefs that took a new shape during the window.

    Read from the notes of what reconciliation decided rather than from the
    beliefs themselves. A belief only knows its own version; the note knows
    that a change happened, when, and what the change was said to be.
    """
    corpus = index.corpus
    changes: list[BeliefChange] = []

    for decision in corpus.decisions:
        if str(decision.get("action") or "") != ReconciliationAction.EVOLVE.value:
            continue
        target_id = str(decision.get("target_node_id") or "")
        source_id = str(decision.get("source_node_id") or "")

        older = corpus.beliefs.get(target_id)
        newer = corpus.beliefs.get(source_id)
        if older is None and newer is None:
            continue

        changes.append(
            BeliefChange(
                belief_id=target_id or source_id,
                old_version=_version_of(older),
                old_content=_statement_of(older),
                new_version=_version_of(newer),
                new_content=_statement_of(newer),
                delta_description=decision.get("delta_description"),
                evolved_on=_moment(decision.get("created_at")),
            )
        )

    changes.sort(key=lambda item: (item.evolved_on or datetime.min, item.belief_id))
    return changes


def repeated_lessons(index: WindowIndex, *, config: MacroConfig) -> list[RepeatedLesson]:
    """
    Lessons the window arrived at more than once.

    These are the most trustworthy things in a report. Something a person
    worked out independently on three separate occasions is not an
    interpretation — it is a finding.
    """
    episode_ids = set(index.date_of)
    threshold = max(config.repeated_lesson_min_episodes, 1)
    found: list[RepeatedLesson] = []

    for lesson in index.corpus.lessons:
        evidence = {
            str(item) for item in (lesson.get("evidence_episodes") or [])
        } & episode_ids
        if len(evidence) < threshold:
            continue
        found.append(
            RepeatedLesson(
                lesson_id=str(lesson.get("node_id")),
                content=str(lesson.get("lesson_statement") or ""),
                appearance_count=len(evidence),
            )
        )

    found.sort(key=lambda item: (-item.appearance_count, item.lesson_id))
    return found


def ignored_lessons(index: WindowIndex, *, config: MacroConfig) -> list[IgnoredLesson]:
    """
    Lessons learned earlier and not touched since.

    Absence is the whole signal, so this is measured from the last time a
    lesson was backed by anything rather than from anything in the window.
    A lesson somebody keeps re-learning and a lesson they have quietly
    dropped look the same on the day it is written down; the difference only
    shows up weeks later.
    """
    window = index.corpus.window
    episode_ids = set(index.date_of)
    cutoff = max(config.ignored_lesson_days, 0)
    found: list[IgnoredLesson] = []

    for lesson in index.corpus.lessons:
        if {str(item) for item in (lesson.get("evidence_episodes") or [])} & episode_ids:
            continue
        last_seen = _moment(lesson.get("valid_from") or lesson.get("created_at"))
        if last_seen is None:
            continue
        quiet_days = index.days_between(last_seen, window.period_end)
        if quiet_days < cutoff:
            continue
        found.append(
            IgnoredLesson(
                lesson_id=str(lesson.get("node_id")),
                content=str(lesson.get("lesson_statement") or ""),
                last_seen=_utc(last_seen).date(),
                days_since_last_seen=quiet_days,
            )
        )

    found.sort(key=lambda item: (-item.days_since_last_seen, item.lesson_id))
    return found[: max(config.ignored_lesson_limit, 1)]


def growth_candidate(index: WindowIndex) -> GrowthCandidate | None:
    """
    The pattern with the best case for calling it progress.

    Chosen by rule so the choice is reproducible: the largest fall in how
    often something fired, among patterns with some record of having been
    worked on rather than merely having gone quiet. Something that stopped
    on its own is a different report line, and it already has one.
    """
    corpus = index.corpus
    best: GrowthCandidate | None = None
    best_drop = 0

    considered = set(corpus.previous_pattern_episodes) | set(index.pattern_episodes)
    for pattern_id in considered:
        was = corpus.previous_pattern_episodes.get(pattern_id, 0)
        now = len(index.pattern_episodes.get(pattern_id, ()))
        drop = was - now
        if drop <= 0:
            continue

        evolved = _was_evolved(corpus, pattern_id)
        regulated = pattern_id in index.regulated
        if not (evolved or regulated):
            continue

        if drop > best_drop:
            best_drop = drop
            best = GrowthCandidate(
                node_id=pattern_id,
                name=index.label_for(pattern_id),
                episode_count=now,
                previous_episode_count=was,
                was_evolved=evolved,
                was_regulated=regulated,
            )

    return best


def struggle_candidate(
    index: WindowIndex, frequency: list[PatternCount]
) -> StruggleCandidate | None:
    """
    The pattern that took up most of the window.

    The count of difficult noticings beside it is exactly that — how many
    were written down in the same pieces of writing. It is not a severity
    score, and it is not called one, because nothing in Lumen records how
    strongly anything was felt.
    """
    if not frequency:
        return None

    leader = frequency[0]
    episodes = index.pattern_episodes.get(leader.pattern_id, set())
    difficult = sum(
        1
        for episode_id in episodes
        for observation in index.observations_of.get(episode_id, ())
        if observation.type in NEGATIVE_AFFECT_TYPES
    )

    return StruggleCandidate(
        pattern_id=leader.pattern_id,
        name=leader.label,
        episode_count=leader.episode_count,
        negative_observation_count=difficult,
    )


def relational_dynamics(
    index: WindowIndex, *, config: MacroConfig
) -> list[RelationalDynamic]:
    """
    Which relationships the window was actually about.

    Grouped by the name written in the entry rather than by the person
    record, because a noticing names people as it found them and not every
    name in an entry has become a record of its own.
    """
    threshold = max(config.relational_min_observations, 1)
    counts: Counter[str] = Counter()
    excerpts: dict[str, list[str]] = defaultdict(list)

    for observations in index.observations_of.values():
        for observation in observations:
            if observation.type != ObservationType.RELATIONAL_DYNAMIC.value:
                continue
            for name in observation.person_refs or ("someone unnamed",):
                counts[name] += 1
                if len(excerpts[name]) < 3:
                    excerpts[name].append(observation.content)

    found = [
        RelationalDynamic(
            person_ref=name,
            person_id=_person_id_for(index, name),
            observation_count=count,
            excerpts=tuple(excerpts[name]),
        )
        for name, count in counts.items()
        if count >= threshold
    ]
    found.sort(key=lambda item: (-item.observation_count, item.person_ref))
    return found


def environment_observations(index: WindowIndex) -> list[EnvironmentObservation]:
    """
    Everything noticed about places the person leans on.

    Handed on as a plain list because grouping them is not a job for code —
    "the office" and "my desk at work" are one place and no rule will ever
    say so. The grouping is asked of the model; the weight behind each group
    is worked out here afterwards.
    """
    return [
        EnvironmentObservation(
            observation_id=observation.node_id,
            content=observation.content,
            signal_strength=observation.signal_strength,
        )
        for observations in index.observations_of.values()
        for observation in observations
        if observation.type == ObservationType.ENVIRONMENTAL_DEPENDENCY.value
    ]


def unresolved_open_loops(
    index: WindowIndex, *, config: MacroConfig
) -> list[OpenLoopFacts]:
    """
    Questions the person is still carrying at the end of the window.

    A loop something in the window actually settled is left out, even if its
    own record has not caught up. The writing that closes a question is
    better evidence than the field that says it is open.
    """
    settled = set(index.corpus.closed_loop_ids)
    window = index.corpus.window
    found: list[OpenLoopFacts] = []

    for loop in index.corpus.open_loops:
        loop_id = str(loop.get("node_id"))
        if loop_id in settled:
            continue
        if str(loop.get("resolution_status") or "OPEN") != "OPEN":
            continue
        raised = _moment(loop.get("valid_from") or loop.get("created_at"))
        found.append(
            OpenLoopFacts(
                open_loop_id=loop_id,
                content=str(loop.get("loop_description") or ""),
                first_raised=raised,
                days_open=index.days_between(raised, window.period_end),
            )
        )

    found.sort(key=lambda item: (-item.days_open, item.open_loop_id))
    return found[: max(config.open_loop_limit, 1)]


def high_signal_observations(
    index: WindowIndex, *, config: MacroConfig
) -> list[HighSignalObservation]:
    """
    The noticings weighty enough to carry whatever else the window held.

    Included on their own merit rather than because they recurred. Some
    things only happen once and are still the most important thing that
    happened, and a report built purely on frequency would lose all of them.
    """
    found = [
        HighSignalObservation(
            observation_id=observation.node_id,
            type=observation.type,
            signal_strength=observation.signal_strength,
            content=observation.content,
            episode_id=episode.episode_id,
        )
        for episode in index.corpus.episodes
        for observation in episode.observations
        if observation.signal_strength in WEIGHTY_SIGNALS
    ]
    return found[: max(config.high_signal_limit, 1)]


def unprocessed_motifs(index: WindowIndex) -> list[UnprocessedMotif]:
    """
    Patterns reached only through feelings that arrived unbidden.

    These are the themes a person has not yet consciously taken hold of. The
    signal is narrow on purpose — one kind of noticing, nothing else — since
    widening it would turn a specific observation into a general one.
    """
    surfacing = {
        observation.node_id
        for observations in index.observations_of.values()
        for observation in observations
        if observation.type == ObservationType.SUPPRESSED_EMOTION_SURFACING.value
    }
    if not surfacing:
        return []

    counts: Counter[str] = Counter()
    for link in index.corpus.links:
        if link.to_type == "pattern" and link.from_id in surfacing:
            counts[link.to_id] += 1

    found = [
        UnprocessedMotif(
            pattern_id=pattern_id,
            label=index.label_for(pattern_id),
            suppressed_surfacing_count=count,
        )
        for pattern_id, count in counts.items()
    ]
    found.sort(key=lambda item: (-item.suppressed_surfacing_count, item.pattern_id))
    return found


def relationship_arcs(
    index: WindowIndex, *, config: MacroConfig
) -> list[RelationshipArcFacts]:
    """
    What can be counted about the relationships that ran through the window.

    Only people who appeared repeatedly. Somebody mentioned once has no arc
    to describe, and inviting a model to describe one anyway is inviting it
    to make something up.
    """
    threshold = max(config.arc_min_episodes, 1)
    found: list[RelationshipArcFacts] = []

    for person_id, episode_ids in index.person_episodes.items():
        if len(episode_ids) < threshold:
            continue
        record = index.corpus.people.get(person_id, {})
        name = str(record.get("canonical_name") or person_id)

        types: Counter[str] = Counter()
        excerpts: list[str] = []
        for episode_id in episode_ids:
            for observation in index.observations_of.get(episode_id, ()):
                if name in (observation.person_refs or ()):
                    types[observation.type] += 1
                    if len(excerpts) < 4:
                        excerpts.append(observation.content)

        found.append(
            RelationshipArcFacts(
                person_id=person_id,
                canonical_name=name,
                episodes_in_window=len(episode_ids),
                dominant_observation_types=tuple(
                    kind for kind, _ in types.most_common(3)
                ),
                stored_direction=_TREND_DIRECTIONS.get(
                    str(record.get("relationship_sentiment_trend") or "")
                ),
                excerpts=tuple(excerpts),
            )
        )

    found.sort(key=lambda item: (-item.episodes_in_window, item.person_id))
    return found


def biographical_gaps(index: WindowIndex) -> list[BiographicalGapFacts]:
    """Things the window noticed were missing from the person's story."""
    return [
        BiographicalGapFacts(
            observation_id=observation.node_id,
            content=observation.content,
            first_raised=episode.occurred_at,
        )
        for episode in index.corpus.episodes
        for observation in episode.observations
        if observation.type == ObservationType.BIOGRAPHICAL_GAP.value
    ]


def active_contradictions(index: WindowIndex) -> list[ContradictionFacts]:
    """
    Tensions recorded in the window that have not resolved.

    Not errors, and not presented as any. Two beliefs that genuinely conflict
    are an ordinary thing for a person to be holding, and the report's job is
    to say so rather than to pick a winner.
    """
    window = index.corpus.window
    found: list[ContradictionFacts] = []

    for record in index.corpus.contradictions:
        if str(record.get("resolution_status") or "UNRESOLVED") != "UNRESOLVED":
            continue
        detected = _moment(record.get("valid_from") or record.get("created_at"))
        found.append(
            ContradictionFacts(
                contradiction_id=str(record.get("node_id")),
                belief_a=_belief_words(index, record.get("belief_a_id")),
                belief_b=_belief_words(index, record.get("belief_b_id")),
                summary=str(record.get("contradiction_summary") or ""),
                first_detected=detected,
                days_open=index.days_between(detected, window.period_end),
            )
        )

    found.sort(key=lambda item: (-item.days_open, item.contradiction_id))
    return found


def _was_evolved(corpus: WindowCorpus, node_id: str) -> bool:
    """Whether a decision in this window took a record to a new shape."""
    return any(
        str(decision.get("action") or "") == ReconciliationAction.EVOLVE.value
        and node_id in (
            str(decision.get("target_node_id") or ""),
            str(decision.get("source_node_id") or ""),
        )
        for decision in corpus.decisions
    )


def _belief_words(index: WindowIndex, belief_id: Any) -> str:
    """
    What one side of a tension actually says.

    Falls back to the identifier when the belief itself was not among the
    records read. A tension is worth reporting even when only one half of it
    can be quoted.
    """
    if not belief_id:
        return ""
    record = index.corpus.beliefs.get(str(belief_id))
    return _statement_of(record) or str(belief_id)


def _person_id_for(index: WindowIndex, name: str) -> str | None:
    """The record for a written name, when one has been made."""
    for person_id, record in index.corpus.people.items():
        if str(record.get("canonical_name") or "") == name:
            return person_id
    return None


def _version_of(record: dict[str, Any] | None) -> int | None:
    """Which version of a belief this is."""
    if not record:
        return None
    return int(record.get("version") or 1)


def _statement_of(record: dict[str, Any] | None) -> str:
    """What a belief says."""
    if not record:
        return ""
    return str(record.get("belief_statement") or "")


def _oldest_pending_days(corpus: WindowCorpus) -> int | None:
    """How long the longest-waiting review item has been waiting."""
    _, oldest = corpus.pending_review
    if oldest is None:
        return None
    return max((_utc(corpus.window.period_end) - _utc(oldest)).days, 0)


def _moment(value: Any) -> datetime | None:
    """Read a stored timestamp back, or nothing if it cannot be read."""
    if isinstance(value, datetime):
        return value
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        logger.debug("could not read %r as a moment", value)
        return None


def _utc(moment: datetime) -> datetime:
    """A moment with a timezone, reading a bare one as UTC."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


__all__ = [
    "NEGATIVE_AFFECT_TYPES",
    "WEIGHTY_SIGNALS",
    "WindowIndex",
    "compute",
    "pattern_frequency",
    "emerging_patterns",
    "disappearing_patterns",
    "belief_changes",
    "repeated_lessons",
    "ignored_lessons",
    "growth_candidate",
    "struggle_candidate",
    "relational_dynamics",
    "environment_observations",
    "unresolved_open_loops",
    "high_signal_observations",
    "unprocessed_motifs",
    "relationship_arcs",
    "biographical_gaps",
    "active_contradictions",
]
