"""
Working out what an erasure would cover, without touching anything.

Two questions, and they are answered very differently.

"Everything" is a walk over every kind of record there is, a page at a time.
It cannot be a single read: a history worth erasing is a history worth years
of writing, and pulling all of it into memory to decide what to rewrite would
be the one place this design fell over.

"One entry" is two narrow lookups. The piece of writing itself, and then
everything that was read out of it — the noticings, the events, the causal
chains — all of which carry the identifier of the episode they came from.

What one entry deliberately does not reach is the standing records built
partly from it. A belief drawn from that evening and nine others is not a
copy of that evening, and rewriting it would erase the other nine as
collateral. That limit is reported rather than left to be discovered.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

from lumen.graph.provider import ReadOnlyGraph
from lumen.graph.queries import tidy_row

logger = logging.getLogger(__name__)

# Every kind of record a sweep walks. Named here rather than read from the
# store's own list, so a new kind of record has to be added deliberately and
# a sweep can never silently start covering something nobody reviewed.
ALL_TABLES: tuple[str, ...] = (
    "EpisodeNode",
    "ObservationNode",
    "EventNode",
    "SessionNode",
    "CausalChainNode",
    "CausalStepNode",
    "PatternNode",
    "BeliefNode",
    "LessonNode",
    "AdoptedPrincipleNode",
    "PersonEntityNode",
    "DecisionAuditNode",
    "ContradictionNode",
    "MacroextractionReportNode",
    "OpenLoopNode",
)

# The kinds of record that carry the identifier of the episode they came out
# of, which is what makes erasing one entry possible at all.
EPISODE_SCOPED_TABLES: tuple[str, ...] = (
    "ObservationNode",
    "EventNode",
    "SessionNode",
    "CausalChainNode",
)

# What erasing a single entry does not cover, in words somebody can act on.
ENTRY_SCOPE_LIMITS: tuple[str, ...] = (
    "standing beliefs and patterns drawn from this entry and others",
    "other people's records, which belong to more than one entry",
)

# How many records to name in the record of an erasure. Enough to prove which
# history it was; not so many that the proof becomes the largest surviving
# description of what existed.
MAX_RECORDED_ENTRY_IDS = 500


class GraphTargets:
    """
    Finds the records an erasure covers.

    Reads and nothing else. Kept apart from the thing that rewrites them so
    that working out the scope can be offered on its own as a preview — the
    one chance anybody gets to look before agreeing to something permanent.
    """

    def __init__(self, graph: ReadOnlyGraph, *, batch_size: int = 200) -> None:
        self._graph = graph
        self._batch = max(int(batch_size), 1)

    def everything(self) -> Iterator[tuple[str, list[str]]]:
        """
        Every record in the graph, a kind and a page at a time.

        Handed back as it is found rather than gathered into one list, so the
        caller can rewrite each page and move on. A whole history never has
        to be in memory at once.
        """
        for table in ALL_TABLES:
            after: str | None = None
            while True:
                page = self._graph.iter_node_ids(
                    table, after=after, limit=self._batch
                )
                if not page:
                    break
                yield table, page
                after = page[-1]

    def for_entry(self, entry_id: str) -> list[str]:
        """
        The records that came out of one piece of writing.

        Empty when nothing was ever written about that entry, which the
        caller reads as "no such entry" — the two are the same thing here,
        since an entry exists only in what was made of it.
        """
        episodes = self._episodes_of(entry_id)
        if not episodes:
            logger.info(
                "nothing in the graph came from that entry",
                extra={"entry_id": entry_id},
            )
            return []

        found: list[str] = list(episodes)
        for table in EPISODE_SCOPED_TABLES:
            found.extend(self._by_episode(table, episodes))

        # Steps belong to chains rather than to episodes, so they are reached
        # through the chains rather than directly.
        chains = self._by_episode("CausalChainNode", episodes)
        found.extend(self._steps_of(chains))

        return list(dict.fromkeys(found))

    def count_by_kind(self, node_ids: list[str]) -> dict[str, int]:
        """How many of these records are of each kind."""
        counts: dict[str, int] = {}
        for row in self._graph.get_nodes_by_ids(node_ids):
            kind = str(row.get("_label") or "Unknown")
            counts[kind] = counts.get(kind, 0) + 1
        return counts

    def _episodes_of(self, entry_id: str) -> list[str]:
        """Which episodes were written from this entry."""
        return [
            str(row["node_id"])
            for row in self._read("EpisodeNode")
            if tidy_row(row).get("entry_id") == entry_id
        ]

    def _by_episode(self, table: str, episodes: list[str]) -> list[str]:
        """Records of one kind belonging to any of these episodes."""
        wanted = set(episodes)
        return [
            str(row["node_id"])
            for row in self._read(table)
            if tidy_row(row).get("episode_id") in wanted
        ]

    def _steps_of(self, chain_ids: list[str]) -> list[str]:
        """The steps making up these causal chains."""
        wanted = set(chain_ids)
        return [
            str(row["node_id"])
            for row in self._read("CausalStepNode")
            if tidy_row(row).get("chain_id") in wanted
        ]

    def _read(self, table: str) -> Iterator[dict]:
        """
        Every row of one kind, a page at a time.

        Reading whole rows rather than identifiers because the columns that
        say which entry a record belongs to are on the row and nowhere else.
        Still paged, so the memory cost is one page rather than one table.
        """
        after: str | None = None
        while True:
            page = self._graph.iter_node_ids(table, after=after, limit=self._batch)
            if not page:
                return
            yield from self._graph.get_nodes_by_ids(page)
            after = page[-1]


__all__ = [
    "ALL_TABLES",
    "EPISODE_SCOPED_TABLES",
    "ENTRY_SCOPE_LIMITS",
    "MAX_RECORDED_ENTRY_IDS",
    "GraphTargets",
]
