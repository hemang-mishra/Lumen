"""
Reading one stretch of somebody's history out of the stores.

This is the only module in the package that touches a database, and that is
the point of it existing separately. Everything downstream works on the object
this returns, so the whole of the arithmetic — which is where the report's
judgements actually live — can be exercised against a handful of dictionaries
with no infrastructure at all.

Three things are read that are not strictly inside the window, each for a
reason the report would be wrong without.

Lessons are read from well before it, because a lesson is "ignored" precisely
by being absent, and nothing absent can be found by searching the window.
Every live pattern is read regardless of date, for the same reason applied to
ageing. And the period before this one is read too, because "this stopped
happening" cannot be said from one period alone.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from lumen.config import MacroConfig
from lumen.graph.provider import ReadOnlyGraph
from lumen.graph.queries import tidy_row
from lumen.operational.repositories import OperationalStore
from lumen.pipeline.macroextraction import windows
from lumen.pipeline.macroextraction.contracts import (
    EpisodeFacts,
    MacroWindow,
    ObservationFacts,
    StandingLink,
    WindowCorpus,
)
from lumen.schemas.enums import ObservationType

logger = logging.getLogger(__name__)

# The links that mean "something noticed turned into something the person
# carries". Following these from a window's findings is how every count about
# a pattern or a belief in the report is arrived at.
STANDING_EDGES: tuple[str, ...] = (
    "same_as_obs_pat",
    "reinforces_obs_pat",
    "reinforces_obs_bel",
    "reinforces_evt_pat",
    "reinforces_evt_bel",
    "branches_to_obs_pat",
    "branches_to_obs_bel",
    "branches_to_evt_pat",
    "branches_to_evt_bel",
    "branches_to_sess_pat",
    "branches_to_sess_bel",
    "regulates_obs",
    "regulates_sess",
    "mentions_obs",
    "mentions_evt",
    "mentions_sess",
    "adopted_as_obs",
    "adopted_as_sess",
)

# Which kind of standing record each link leads to. Read off the link name
# rather than by fetching the record, so a batch of several hundred links can
# be sorted without a query per link.
_PATTERN_SUFFIXES: tuple[str, ...] = ("_pat", "regulates_obs", "regulates_sess")
_BELIEF_SUFFIXES: tuple[str, ...] = ("_bel",)
_PERSON_EDGES: frozenset[str] = frozenset({"mentions_obs", "mentions_evt", "mentions_sess"})

# The observation type that means the person caught themselves in the act.
AWARENESS_TYPE = ObservationType.METACOGNITIVE_INTERRUPT.value


def gather(
    window: MacroWindow,
    *,
    graph: ReadOnlyGraph,
    ops: OperationalStore | None = None,
    config: MacroConfig,
    user_id: str = "local",
) -> WindowCorpus:
    """
    Everything one report is allowed to see.

    The stores arrive as parameters rather than being reached for, which is
    what lets this be pointed at a seeded database in a test and at the real
    one in a run, and what makes the list of things it touches readable in
    its own signature.

    The graph arrives as a reader. Nothing in the reading half of a report
    can change anything, and that is a property of the type rather than a
    matter of care.
    """
    episodes, truncated = _read_episodes(window, graph=graph, config=config)
    if not episodes:
        return WindowCorpus(window=window, truncated=truncated)

    links = _read_links(episodes, graph=graph)
    targets = _sort_targets(links)

    earlier = windows.previous_window(window)
    previous_counts, previous_total = _count_previous(earlier, graph=graph, config=config)
    comparison_counts, comparison_awareness = _count_comparison(
        window, graph=graph, config=config
    )

    return WindowCorpus(
        window=window,
        episodes=episodes,
        links=links,
        patterns=_read_nodes(targets["pattern"], graph=graph),
        beliefs=_read_nodes(targets["belief"], graph=graph),
        people=_read_nodes(targets["person"], graph=graph),
        lessons=_read_lessons(window, graph=graph, config=config),
        contradictions=_read_by_type(
            "ContradictionNode", window, graph=graph, config=config
        ),
        open_loops=_read_by_type("OpenLoopNode", window, graph=graph, config=config),
        decisions=_read_by_type(
            "DecisionAuditNode", window, graph=graph, config=config
        ),
        all_patterns=_read_all_patterns(graph=graph, config=config),
        previous_pattern_frequency=_as_percentages(previous_counts, previous_total),
        previous_pattern_episodes=previous_counts,
        previous_episode_count=previous_total,
        comparison_counts=comparison_counts,
        awareness_counts=_awareness_counts(episodes, links),
        previous_awareness_counts=comparison_awareness,
        closed_loop_ids=_read_closed_loops(episodes, graph=graph),
        pending_review=_read_pending_review(ops, user_id=user_id),
        truncated=truncated,
    )


def _read_episodes(
    window: MacroWindow, *, graph: ReadOnlyGraph, config: MacroConfig
) -> tuple[list[EpisodeFacts], bool]:
    """
    The writing about this stretch of time, with what each piece produced.

    One extra query per episode. That is a few dozen for a month and it is
    the honest cost of the question — an episode's contents are the only
    place its observations can be found, and there is no batched form of
    "everything these thirty pieces of writing produced".
    """
    cap = max(config.max_episodes_per_window, 1)
    rows = graph.find_episodes_by_event_date(
        window.start_date, window.end_date, limit=cap + 1
    )

    truncated = len(rows) > cap
    if truncated:
        rows = rows[:cap]
        logger.warning(
            "more writing in this period than one report will read",
            extra={
                "period_start": window.period_start.isoformat(),
                "report_type": window.report_type.value,
                "cap": cap,
            },
        )

    return [_episode_facts(row, graph=graph) for row in rows], truncated


def _episode_facts(row: dict[str, Any], *, graph: ReadOnlyGraph) -> EpisodeFacts:
    """One piece of writing and everything it produced, in report terms."""
    episode = tidy_row(row)
    episode_id = str(episode.get("node_id", ""))
    contents = graph.get_episode_contents(episode_id)

    observations: list[ObservationFacts] = []
    finding_ids: list[str] = []

    for child in contents.nodes:
        tidied = tidy_row(child)
        node_id = str(tidied.get("node_id", ""))
        if node_id in ("", episode_id):
            continue
        label = str(child.get("_label", ""))

        if label == "ObservationNode":
            finding_ids.append(node_id)
            observations.append(
                ObservationFacts(
                    node_id=node_id,
                    type=str(tidied.get("type", "")) or "CONTEXT",
                    content=str(tidied.get("content", "")),
                    signal_strength=str(tidied.get("signal_strength", "")),
                    person_refs=tuple(tidied.get("person_refs") or ()),
                    episode_id=episode_id,
                )
            )
        elif label in ("EventNode", "SessionNode"):
            finding_ids.append(node_id)

    return EpisodeFacts(
        episode_id=episode_id,
        event_date=_as_date(episode.get("event_date")),
        occurred_at=_as_datetime(episode.get("occurred_at")),
        episode_summary=str(episode.get("episode_summary", "")),
        historical_era=episode.get("historical_era"),
        observations=tuple(observations),
        finding_ids=tuple(finding_ids),
    )


def _read_links(
    episodes: list[EpisodeFacts], *, graph: ReadOnlyGraph
) -> list[StandingLink]:
    """What all of the window's findings turned into, asked in one go."""
    finding_ids = [
        finding_id for episode in episodes for finding_id in episode.finding_ids
    ]
    if not finding_ids:
        return []

    return [
        StandingLink(
            from_id=edge.from_node_id,
            to_id=edge.to_node_id,
            to_type=_target_kind(edge.edge_type),
            edge_name=edge.edge_type,
        )
        for edge in graph.find_standing_edges(
            finding_ids, edge_names=list(STANDING_EDGES)
        )
    ]


def _target_kind(edge_name: str) -> str:
    """Which kind of standing record a link leads to, from its name alone."""
    if edge_name in _PERSON_EDGES:
        return "person"
    if edge_name.startswith("adopted_as"):
        return "principle"
    if any(edge_name.endswith(suffix) for suffix in _BELIEF_SUFFIXES):
        return "belief"
    if any(
        edge_name.endswith(suffix) or edge_name == suffix
        for suffix in _PATTERN_SUFFIXES
    ):
        return "pattern"
    return "other"


def _sort_targets(links: list[StandingLink]) -> dict[str, list[str]]:
    """
    Group the far end of every link by which kind of record it is.

    Every kind is present in the answer even when nothing pointed at it, so
    a period in which nobody was mentioned reads as "no people" rather than
    as a missing key at the point of use.
    """
    grouped: dict[str, list[str]] = defaultdict(list)
    for link in links:
        grouped[link.to_type].append(link.to_id)

    return {
        kind: list(dict.fromkeys(grouped.get(kind, [])))
        for kind in ("pattern", "belief", "person", "principle", "other")
    }


def _read_nodes(
    node_ids: list[str] | None, *, graph: ReadOnlyGraph
) -> dict[str, dict[str, Any]]:
    """Fetch a set of records and key them by id."""
    if not node_ids:
        return {}
    return {
        str(row.get("node_id")): tidy_row(row)
        for row in graph.get_nodes_by_ids(node_ids)
        if row.get("node_id")
    }


def _read_by_type(
    node_type: str,
    window: MacroWindow,
    *,
    graph: ReadOnlyGraph,
    config: MacroConfig,
    since: datetime | None = None,
) -> list[dict[str, Any]]:
    """Every record of one kind that belongs to the window."""
    return [
        tidy_row(row)
        for row in graph.find_nodes(
            [node_type],
            since=since or window.period_start,
            until=window.period_end,
            active_only=False,
            limit=max(config.max_nodes_per_kind, 1),
        )
    ]


def _read_lessons(
    window: MacroWindow, *, graph: ReadOnlyGraph, config: MacroConfig
) -> list[dict[str, Any]]:
    """
    Lessons from the window and from a good stretch before it.

    Reaching back is what makes an ignored lesson findable. A lesson that has
    not been touched in six weeks leaves no trace inside the window at all,
    so looking only there would report that there are none.
    """
    lookback = window.period_start - timedelta(
        days=max(config.ignored_lesson_lookback_days, 1)
    )
    return _read_by_type(
        "LessonNode", window, graph=graph, config=config, since=lookback
    )


def _read_all_patterns(
    *, graph: ReadOnlyGraph, config: MacroConfig
) -> list[dict[str, Any]]:
    """
    Every pattern still in play, whatever its date.

    Not limited to the window on purpose. Ageing is a statement about absence
    — the patterns worth reporting on here are exactly the ones that did not
    appear.
    """
    return [
        tidy_row(row)
        for row in graph.find_nodes(
            ["PatternNode"], active_only=True, limit=max(config.max_nodes_per_kind, 1)
        )
    ]


def _count_previous(
    earlier: MacroWindow, *, graph: ReadOnlyGraph, config: MacroConfig
) -> tuple[dict[str, int], int]:
    """
    How often each pattern fired in the period before this one.

    Read directly rather than taken from that period's own report, so the
    comparison holds even for a period that was never reported on — a
    catch-up run has no earlier report to lean on, and a month whose report
    is missing should not silently lose its "this stopped" section.

    Only the links are read, not the contents. Which patterns fired and in
    how many pieces of writing is all this is for.
    """
    rows = graph.find_episodes_by_event_date(
        earlier.start_date, earlier.end_date, limit=max(config.max_episodes_per_window, 1)
    )
    if not rows:
        return {}, 0

    episode_ids = [str(row.get("node_id")) for row in rows if row.get("node_id")]
    counts = _pattern_episode_counts(episode_ids, graph=graph)
    return counts, len(episode_ids)


def _count_comparison(
    window: MacroWindow, *, graph: ReadOnlyGraph, config: MacroConfig
) -> tuple[dict[str, int], dict[str, int]]:
    """
    The longer run of history a shift is measured against.

    Only gathered for the kinds of report long enough for the comparison to
    mean something. A week held up against the previous quarter says more
    about the difference in length than about the person.
    """
    if window.report_type.value not in ("MONTHLY", "QUARTERLY"):
        return {}, {}

    earlier = windows.comparison_window(window, config=config)
    rows = graph.find_episodes_by_event_date(
        earlier.start_date,
        earlier.end_date,
        limit=max(config.max_episodes_per_window, 1),
    )
    episode_ids = [str(row.get("node_id")) for row in rows if row.get("node_id")]
    if not episode_ids:
        return {}, {}

    return (
        _pattern_episode_counts(episode_ids, graph=graph),
        _pattern_awareness_counts(episode_ids, graph=graph),
    )


def _findings_by_episode(
    episode_ids: list[str], *, graph: ReadOnlyGraph
) -> dict[str, str]:
    """
    Which piece of writing each finding came out of.

    Read from the containment links rather than by fetching every finding,
    because the link already names both ends and the alternative is fetching
    several hundred records to learn one field of each.
    """
    return {
        edge.to_node_id: edge.from_node_id
        for edge in graph.find_standing_edges(
            episode_ids,
            edge_names=["contains_obs", "contains_evt", "contains_sess"],
            include_invalidated=True,
        )
    }


def _pattern_episode_counts(
    episode_ids: list[str], *, graph: ReadOnlyGraph
) -> dict[str, int]:
    """How many separate pieces of writing each pattern appeared in."""
    owner = _findings_by_episode(episode_ids, graph=graph)
    if not owner:
        return {}

    seen: dict[str, set[str]] = defaultdict(set)
    for edge in graph.find_standing_edges(
        list(owner), edge_names=list(STANDING_EDGES)
    ):
        if _target_kind(edge.edge_type) != "pattern":
            continue
        seen[edge.to_node_id].add(owner[edge.from_node_id])

    return {pattern_id: len(episodes) for pattern_id, episodes in seen.items()}


def _pattern_awareness_counts(
    episode_ids: list[str], *, graph: ReadOnlyGraph
) -> dict[str, int]:
    """
    How often each pattern was caught in the act.

    Counted from the observations that record somebody noticing themselves
    mid-pattern. A habit that happens as much as it used to but is now seen
    while it happens has changed, and counting only how often it fired would
    call that no change at all.
    """
    owner = _findings_by_episode(episode_ids, graph=graph)
    if not owner:
        return {}

    awareness_ids = {
        str(row.get("node_id"))
        for row in graph.get_nodes_by_ids(list(owner))
        if str(row.get("type") or "") == AWARENESS_TYPE
    }
    if not awareness_ids:
        return {}

    counts: dict[str, int] = defaultdict(int)
    for edge in graph.find_standing_edges(
        sorted(awareness_ids), edge_names=list(STANDING_EDGES)
    ):
        if _target_kind(edge.edge_type) == "pattern":
            counts[edge.to_node_id] += 1
    return dict(counts)


def _awareness_counts(
    episodes: list[EpisodeFacts], links: list[StandingLink]
) -> dict[str, int]:
    """The same count for the window itself, from what has already been read."""
    aware = {
        observation.node_id
        for episode in episodes
        for observation in episode.observations
        if observation.type == AWARENESS_TYPE
    }
    counts: dict[str, int] = defaultdict(int)
    for link in links:
        if link.to_type == "pattern" and link.from_id in aware:
            counts[link.to_id] += 1
    return dict(counts)


def _read_closed_loops(
    episodes: list[EpisodeFacts], *, graph: ReadOnlyGraph
) -> tuple[str, ...]:
    """Which open questions the window's writing actually settled."""
    episode_ids = [episode.episode_id for episode in episodes]
    if not episode_ids:
        return ()
    return tuple(
        sorted(
            {
                edge.to_node_id
                for edge in graph.find_standing_edges(episode_ids, edge_names=["closes"])
            }
        )
    )


def _read_pending_review(
    ops: OperationalStore | None, *, user_id: str
) -> tuple[int, datetime | None]:
    """
    How much is waiting for the person to decide, and since when.

    Absent when no operational store was handed over, which is the case in
    tests that only care about the graph. Reported as nothing pending rather
    than refused: a report is about somebody's history, and the state of a
    review queue is a footnote in it.
    """
    if ops is None:
        return (0, None)
    return (ops.hitl.count_pending(user_id), ops.hitl.oldest_pending_at(user_id))


def _as_percentages(counts: dict[str, int], total: int) -> dict[str, float]:
    """Turn raw counts into shares of a period, so lengths can be compared."""
    if total <= 0:
        return {}
    return {
        pattern_id: round(count / total * 100, 1) for pattern_id, count in counts.items()
    }


def _as_date(value: Any) -> date:
    """Read a stored day back, falling back to the earliest possible one."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        logger.debug("could not read %r as a day", value)
        return date.min


def _as_datetime(value: Any) -> datetime:
    """Read a stored moment back, falling back to the earliest possible one."""
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        logger.debug("could not read %r as a moment", value)
        return datetime.min


__all__ = ["STANDING_EDGES", "AWARENESS_TYPE", "gather"]
