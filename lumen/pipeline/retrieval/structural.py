"""
Finding history by what it is attached to, rather than by what it says.

This is the half of retrieval that does not measure similarity at all, and
it exists because of one specific failure. Someone describing recovery uses
none of the words they used describing the injury. "I couldn't be in that
house without my chest going tight" and "I cooked a proper meal there
yesterday and it was just a kitchen" are the same thread, years apart, and
no measure of distance between those two sentences will ever connect them.

So these lookups do not read either sentence. They follow the person named,
the period referred to, and the material still marked unresolved — three
anchors that hold regardless of how the wording drifted.

Each anchor is asked independently and each failure is contained. Losing
one anchor should cost that anchor, not the other two and not the semantic
half of retrieval.
"""

from __future__ import annotations

import logging

from lumen.config import PipelineConfig
from lumen.graph.provider import GraphProvider
from lumen.pipeline.retrieval.hydrate import to_candidates
from lumen.schemas.enums import ObservationType, StructuralAnchorType
from lumen.schemas.pipeline import CandidateNode

logger = logging.getLogger(__name__)

# The node kinds a person can be linked to directly. Beliefs and patterns
# are reached only through the observation that produced them, which is a
# second hop nothing needs yet.
PERSON_LINKED_TYPES = ["ObservationNode", "EventNode", "SessionNode"]

# The node kinds that record which period of the person's life they belong
# to.
ERA_TAGGED_TYPES = ["PatternNode", "BeliefNode", "EpisodeNode"]

# The findings weighty enough that unresolved ones are always worth
# surfacing again, whatever today's entry happens to be about.
UNRESOLVED_TYPES = [
    ObservationType.INAUTHENTICITY_STATE.value,
    ObservationType.IDENTITY_FUSION_STATE.value,
    ObservationType.EXISTENTIAL_REFLECTION.value,
    ObservationType.SUPPRESSED_EMOTION_SURFACING.value,
]


def find_by_anchors(
    *,
    people: tuple[str, ...],
    era: str | None,
    graph: GraphProvider,
    config: PipelineConfig,
) -> list[CandidateNode]:
    """
    Gather everything the episode's anchors reach.

    Run once for a whole episode rather than once per finding: the people
    and the period belong to the episode, and every finding in it shares
    them. Asking per finding would repeat identical queries a dozen times
    for one entry.

    People named in an entry will find nothing until reconciliation has
    started creating person nodes and the edges that point at them. That is
    expected on a young graph, and is why an empty result here is never
    treated as a problem.
    """
    found: list[CandidateNode] = []
    for name in people:
        found.extend(_by_person(name, graph=graph, limit=config.pass_b_keep))
    if era:
        found.extend(_by_era(era, graph=graph, limit=config.pass_b_keep))
    found.extend(_unresolved(graph=graph, limit=config.pass_b_keep))
    return found


def _by_person(
    name: str, *, graph: GraphProvider, limit: int
) -> list[CandidateNode]:
    """Everything active that mentions this person."""
    rows = _attempt(
        "person",
        lambda: graph.find_linked_to_person(
            name, node_types=PERSON_LINKED_TYPES, limit=limit
        ),
    )
    return to_candidates(rows, anchor=StructuralAnchorType.NAMED_PERSON, value=name)


def _by_era(era: str, *, graph: GraphProvider, limit: int) -> list[CandidateNode]:
    """Everything filed under the period the entry referred to."""
    rows = _attempt(
        "era",
        lambda: graph.find_by_era(era, node_types=ERA_TAGGED_TYPES, limit=limit),
    )
    return to_candidates(rows, anchor=StructuralAnchorType.HISTORICAL_ERA, value=era)


def _unresolved(*, graph: GraphProvider, limit: int) -> list[CandidateNode]:
    """
    Weighty findings whose episode is still waiting on reconciliation.

    Surfaced regardless of what today's entry says, because the entry that
    finally resolves one of these will almost never resemble it.
    """
    rows = _attempt(
        "unresolved",
        lambda: graph.find_unresolved_high_signal(UNRESOLVED_TYPES, limit=limit),
    )
    return to_candidates(
        rows,
        anchor=StructuralAnchorType.HIGH_SENSITIVITY_OPEN,
        value="pending reconciliation",
    )


def _attempt(anchor: str, lookup) -> list[dict]:
    """
    Run one anchor lookup, and let it fail on its own.

    A graph that refuses one query should cost that anchor and nothing
    else. Returning an empty list here is safe in a way it would not be for
    the stage as a whole: the anchors are additive, and the caller reports
    the semantic half separately.
    """
    try:
        return lookup()
    except Exception as exc:  # noqa: BLE001 — a broken anchor must not stop the rest
        logger.warning(
            "an anchor lookup failed and was skipped",
            extra={"anchor": anchor, "reason": type(exc).__name__},
        )
        return []


__all__ = [
    "find_by_anchors",
    "PERSON_LINKED_TYPES",
    "ERA_TAGGED_TYPES",
    "UNRESOLVED_TYPES",
]
