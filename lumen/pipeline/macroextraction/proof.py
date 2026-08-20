"""
Laying out the evidence that a long-running pattern is real.

Every other section of a report looks at one stretch of time. This one looks
at all of it, which is why it is here rather than with the rest: when
somebody has rediscovered the same thing about themselves fourteen times over
five years, the fact worth showing them is the fourteen and the five, and no
single month can see either.

Two rules keep it honest.

A chain has to clear a threshold of separate occasions before it exists at
all. Three instances is a coincidence somebody can argue with; ten across
years is not, and that is the difference between showing a person evidence
and showing them a suggestion.

The examples are spread across the years the pattern covers rather than
picked as the most striking. "Most distinct" is not something that can be
computed the same way twice, and one chosen by a model would be a different
five every time it ran. Spread is arithmetic — the first, the last, and
evenly between — and it serves the point better anyway: what makes a chain
convincing is the same thing happening in circumstances that had nothing else
in common.

The numbers here are counted, never described by a model. A model is shown
them afterwards and asked only for a sentence.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from lumen.config import MaintenanceConfig
from lumen.graph.provider import ReadOnlyGraph
from lumen.graph.queries import tidy_row
from lumen.graph.rows import RETIRED_STATUSES, happened_at, preview_of
from lumen.pipeline.macroextraction.contracts import ProofChain, ProofInstance

logger = logging.getLogger(__name__)

# The kinds of standing record worth proving. Named rather than derived, so a
# new kind has to be considered rather than silently starting to appear.
PROVABLE_TABLES: tuple[str, ...] = ("PatternNode", "LessonNode")

# The links that mean "this finding is evidence of that pattern". Also named
# rather than derived: a link added later should have to be thought about
# before it starts counting as proof of anything.
EVIDENCE_EDGES: tuple[str, ...] = (
    "same_as_obs_pat",
    "reinforces_obs_pat",
    "reinforces_evt_pat",
    "branches_to_obs_pat",
    "branches_to_evt_pat",
    "branches_to_sess_pat",
    "regulates_obs",
    "regulates_sess",
)

# A lesson keeps its evidence on itself rather than through links, so the two
# kinds are traced two different ways. That is a fact about how lessons are
# written, not a special case invented here.
LESSON_TABLE = "LessonNode"
LESSON_EVIDENCE_COLUMN = "evidence_episodes"

# How many findings one standing record may be traced back through. A ceiling
# rather than a guess: a pattern with thousands of findings is still proved by
# the first few hundred, and the alternative is one query with no limit at all.
MAX_FINDINGS_PER_RECORD = 500


def find_proof_chains(
    graph: ReadOnlyGraph,
    *,
    config: MaintenanceConfig | None = None,
    page_size: int = 200,
) -> list[ProofChain]:
    """
    Every standing record with enough separate occasions behind it.

    Walks the whole graph a page at a time. Nobody is waiting on this — it
    runs with a report, not with a conversation — and holding every pattern
    somebody has ever had in memory at once is the one thing that would stop
    this working for the histories it is most worth running on.

    Longest chains first, since the point of the section is the strongest
    evidence rather than an inventory.
    """
    settings = config or MaintenanceConfig()
    minimum = max(settings.proof_min_instances, 2)

    chains: list[ProofChain] = []
    for table in PROVABLE_TABLES:
        for node_id in _walk(graph, table, page_size=page_size):
            chain = _chain_for(
                graph, node_id, minimum=minimum, keep=settings.proof_key_instances
            )
            if chain is not None:
                chains.append(chain)

    chains.sort(key=lambda chain: (-chain.total_instances, chain.record_id))
    logger.info(
        "looked over the whole history for long-running patterns",
        extra={"chains": len(chains), "minimum": minimum},
    )
    return chains


def _walk(graph: ReadOnlyGraph, table: str, *, page_size: int) -> Iterable[str]:
    """Every record of one kind, a page at a time."""
    after: str | None = None
    while True:
        page = graph.iter_node_ids(table, after=after, limit=page_size)
        if not page:
            return
        yield from page
        after = page[-1]


def _chain_for(
    graph: ReadOnlyGraph, node_id: str, *, minimum: int, keep: int
) -> ProofChain | None:
    """
    One record's history, if it has enough of one to be worth showing.

    Nothing comes back for a record that has been superseded or suspended.
    Proving at length that somebody used to think something they have since
    revised is the opposite of useful.
    """
    row = graph.get_node(node_id)
    if row is None or _is_retired(row):
        return None

    instances = _instances(graph, node_id, row=row)
    if len(instances) < minimum:
        return None

    ordered = sorted(instances, key=lambda item: item.happened_at)
    return ProofChain(
        record_id=node_id,
        record_type=str(row.get("_label") or "Unknown"),
        label=preview_of(row),
        total_instances=len(ordered),
        span_days=_span_days(ordered),
        first_seen=ordered[0].happened_at,
        last_seen=ordered[-1].happened_at,
        key_instances=_spread(ordered, keep),
    )


def _instances(
    graph: ReadOnlyGraph, node_id: str, *, row: dict
) -> list[ProofInstance]:
    """
    The separate occasions behind one standing record.

    Counted in episodes rather than in findings. One evening that circles the
    same realisation four times is one occasion of it, and counting the
    findings would let a single talkative night look like a month of them.
    """
    if str(row.get("_label") or "") == LESSON_TABLE:
        return _episodes_named_by(graph, tidy_row(row))
    return _episodes_linked_to(graph, node_id)


def _episodes_named_by(graph: ReadOnlyGraph, row: dict) -> list[ProofInstance]:
    """
    The occasions a lesson names for itself.

    Lessons are the one standing record with no links back to what taught
    them; the episodes are written on the lesson. The episodes are read for
    their dates, since a list of identifiers cannot say how far a chain
    reaches.
    """
    episode_ids = [
        str(item) for item in (row.get(LESSON_EVIDENCE_COLUMN) or []) if item
    ]
    if not episode_ids:
        return []

    found: list[ProofInstance] = []
    for episode in graph.get_nodes_by_ids(episode_ids[:MAX_FINDINGS_PER_RECORD]):
        tidied = tidy_row(episode)
        when = happened_at(tidied)
        if when is None:
            continue
        found.append(
            ProofInstance(
                episode_id=str(tidied.get("node_id")),
                happened_at=when,
                excerpt=preview_of(episode),
            )
        )
    return found


def _episodes_linked_to(graph: ReadOnlyGraph, node_id: str) -> list[ProofInstance]:
    """The occasions that point at a pattern through a link."""
    slice_ = graph.get_neighborhood(
        node_id,
        depth=1,
        edge_types=list(EVIDENCE_EDGES),
        direction="in",
        limit=MAX_FINDINGS_PER_RECORD,
    )

    rows = {
        str(row.get("node_id")): tidy_row(row)
        for row in slice_.nodes
        if row.get("node_id") and str(row.get("node_id")) != node_id
    }

    by_episode: dict[str, ProofInstance] = {}
    for finding in rows.values():
        episode_id = str(finding.get("episode_id") or "")
        when = happened_at(finding)
        if not episode_id or when is None or episode_id in by_episode:
            continue
        by_episode[episode_id] = ProofInstance(
            episode_id=episode_id,
            happened_at=when,
            excerpt=preview_of(finding),
        )

    return list(by_episode.values())


def _spread(ordered: list[ProofInstance], keep: int) -> list[ProofInstance]:
    """
    A handful of occasions spread evenly across the years they cover.

    Always the first and the last, because those are what a span is made of,
    with the rest at even intervals between. Asking for more than there are
    gives all of them rather than repeating any.
    """
    wanted = max(int(keep), 1)
    if len(ordered) <= wanted:
        return list(ordered)
    if wanted == 1:
        return [ordered[0]]

    last = len(ordered) - 1
    picked = {round(last * step / (wanted - 1)) for step in range(wanted)}
    return [ordered[index] for index in sorted(picked)]


def _span_days(ordered: list[ProofInstance]) -> int:
    """How long the chain reaches back, in whole days."""
    return max((ordered[-1].happened_at - ordered[0].happened_at).days, 0)


def _is_retired(row: dict) -> bool:
    """Whether a record has been superseded or suspended."""
    return str(row.get("status") or "") in RETIRED_STATUSES


__all__ = [
    "PROVABLE_TABLES",
    "EVIDENCE_EDGES",
    "MAX_FINDINGS_PER_RECORD",
    "find_proof_chains",
]
