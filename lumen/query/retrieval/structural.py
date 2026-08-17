"""
Pass B — finding history by what it is attached to.

This is the half of the search that reads no text at all, and it exists
because of one specific failure that no amount of better matching will fix.
Somebody describing recovery uses none of the words they used describing the
injury. "I couldn't be in that house without my chest going tight" and "I
cooked a proper meal there yesterday and it was just a kitchen" are the same
thread years apart, and no measure of distance between those two sentences
will ever connect them.

So these lookups follow anchors instead: the person named, the period
referred to, the questions still unfinished, the standing records that a
claim of change would be a claim about. Anchors hold however much the
wording drifted.

Which lookups run is decided by the reason the turn gave, as a table. A
reason nobody wrote a lookup for simply gets none, and that shows up as a
missing row rather than as something quietly falling through to whatever the
last branch happened to do.

Every lookup fails alone. Anchors are additive — losing one should cost one.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from lumen.config import QueryConfig
from lumen.graph.provider import ReadOnlyGraph
from lumen.query.retrieval.contracts import RetrievedNode
from lumen.query.retrieval.hydrate import has_id, to_node
from lumen.schemas.enums import (
    RetrievalPass,
    StructuralAnchorType,
    TriggerType,
)
from lumen.schemas.query import RetrievalTrigger

logger = logging.getLogger(__name__)

# What is worth pulling up when a person is named.
#
# The first three mention a person directly. Beliefs and patterns never do —
# they are about somebody because a note about that person turned into
# them — and they are reached one step further on. Both halves matter:
# "what do I know about Alex" means the same thing whether the answer is a
# note from Tuesday or the standing pattern that grew out of it.
PERSON_LINKED_TYPES = [
    "ObservationNode",
    "EventNode",
    "SessionNode",
    "PatternNode",
    "BeliefNode",
]

# What records a period of life is written on.
ERA_TAGGED_TYPES = ["PatternNode", "BeliefNode", "EpisodeNode"]

# The standing records a claim of change could be a claim about.
STANDING_TYPES = ["PatternNode", "BeliefNode"]

OPEN_LOOP_TYPES = ["OpenLoopNode"]
BELIEF_TYPES = ["BeliefNode"]


@dataclass(frozen=True)
class AnchorContext:
    """
    Everything the lookups need, gathered once for the whole turn.

    Attributes:
        graph: The graph, to read from.
        limit: How many records one anchor may contribute.
        base_score: What an exact anchor match counts as when ordering a
            list that also holds measured matches.
    """

    graph: ReadOnlyGraph
    limit: int
    base_score: float


Lookup = Callable[[RetrievalTrigger, AnchorContext], list[RetrievedNode]]


def has_anchors(triggers: tuple[RetrievalTrigger, ...]) -> bool:
    """
    Whether any of these reasons has an anchor to follow at all.

    Asked so a turn can tell "the graph was consulted and said nothing" from
    "there was nothing here to consult it about". Several reasons are
    answered by meaning alone, and a pass with no work to do should not
    count as evidence that the store is reachable.
    """
    return any(LOOKUPS.get(trigger.trigger_type) for trigger in triggers)


def find_by_anchors(
    triggers: tuple[RetrievalTrigger, ...],
    *,
    graph: ReadOnlyGraph,
    config: QueryConfig,
) -> list[RetrievedNode]:
    """
    Follow every anchor the turn's reasons point at.

    Reasons with no structural lookup contribute nothing here and are
    answered by the meaning-based search instead. A turn that produces only
    such reasons gets an empty list from this pass, which is a correct
    answer and not a failure.
    """
    context = AnchorContext(
        graph=graph,
        limit=max(config.conversational_pass_b_keep, 0),
        base_score=config.anchor_base_score,
    )

    found: list[RetrievedNode] = []
    for trigger in triggers:
        for lookup in LOOKUPS.get(trigger.trigger_type, ()):
            found.extend(lookup(trigger, context))
    return found


# ---------------------------------------------------------------------------
# The lookups, one job each
# ---------------------------------------------------------------------------


def _by_person(
    trigger: RetrievalTrigger, context: AnchorContext
) -> list[RetrievedNode]:
    """
    Everything that mentions the people this turn named.

    The turn arrives holding record identifiers, because the reasons were
    already checked against the graph before they got here, while the read
    that finds linked records is keyed by the person's name as it was
    written down. So each record is read first for its own spelling. That
    is one cheap read, and it keeps both sides using the single named
    question that already exists rather than adding a second one meaning
    the same thing.
    """
    found: list[RetrievedNode] = []
    for person_id in trigger.person_node_ids:
        name = _canonical_name(person_id, context.graph)
        if name is None:
            continue
        rows = _attempt(
            "person",
            lambda name=name: context.graph.find_linked_to_person(
                name, node_types=PERSON_LINKED_TYPES, limit=context.limit
            ),
        )
        found.extend(
            _as_nodes(
                rows,
                trigger=trigger,
                context=context,
                anchor=StructuralAnchorType.NAMED_PERSON,
                value=name,
            )
        )
    return found


def _by_era(trigger: RetrievalTrigger, context: AnchorContext) -> list[RetrievedNode]:
    """
    Everything filed under the period of life the turn referred to.

    The spelling used here is the graph's own — the reason was rewritten to
    it when it was checked — because nothing constrains how a period gets
    written down and only the stored spelling will match.
    """
    if not trigger.era:
        return []
    rows = _attempt(
        "era",
        lambda: context.graph.find_by_era(
            trigger.era or "", node_types=ERA_TAGGED_TYPES, limit=context.limit
        ),
    )
    return _as_nodes(
        rows,
        trigger=trigger,
        context=context,
        anchor=StructuralAnchorType.HISTORICAL_ERA,
        value=trigger.era,
    )


def _open_questions(
    trigger: RetrievalTrigger, context: AnchorContext
) -> list[RetrievedNode]:
    """
    The questions this person left unfinished.

    Surfaced whole rather than matched: which one the turn is circling back
    to is a question about meaning, and answering it here would mean doing
    the meaning-based search a second time.
    """
    rows = _attempt(
        "open_loops",
        lambda: context.graph.find_nodes(OPEN_LOOP_TYPES, limit=context.limit),
    )
    return _as_nodes(
        rows,
        trigger=trigger,
        context=context,
        anchor=StructuralAnchorType.HIGH_SENSITIVITY_OPEN,
        value="unfinished",
    )


def _standing_records(
    trigger: RetrievalTrigger, context: AnchorContext
) -> list[RetrievedNode]:
    """
    The patterns and beliefs a claim of improvement would be about.

    This is what "has this actually closed?" needs. Somebody saying they do
    not feel that anymore is making a claim about a specific standing
    record, and the only way to know whether they are right is to have the
    record in hand.
    """
    return _find_standing(trigger, context, STANDING_TYPES)


def _standing_beliefs(
    trigger: RetrievalTrigger, context: AnchorContext
) -> list[RetrievedNode]:
    """The beliefs a turn is questioning, so the AI can see what is being doubted."""
    return _find_standing(trigger, context, BELIEF_TYPES)


def _find_standing(
    trigger: RetrievalTrigger,
    context: AnchorContext,
    node_types: list[str],
) -> list[RetrievedNode]:
    """
    Current standing records, narrowed to an area of life when one was named.

    Without an area this is the person's whole live self-model, which is
    both large and mostly irrelevant to any one sentence — so the limit is
    doing real work here, and the ranking afterwards decides what survives.
    """
    domain = trigger.domain.value if trigger.domain else None
    rows = _attempt(
        "standing",
        lambda: context.graph.find_nodes(
            node_types, domain=domain, active_only=True, limit=context.limit
        ),
    )
    return _as_nodes(
        rows,
        trigger=trigger,
        context=context,
        anchor=StructuralAnchorType.HIGH_SENSITIVITY_OPEN,
        value=domain or "current self-model",
    )


# Which lookups each reason gets.
#
# A table rather than a chain of conditions: a new reason is a new row, and
# a reason with no structural half is visibly absent rather than quietly
# handled by whatever came last.
LOOKUPS: dict[TriggerType, tuple[Lookup, ...]] = {
    TriggerType.NAMED_PERSON: (_by_person,),
    TriggerType.HISTORICAL_ERA: (_by_era,),
    TriggerType.OPEN_LOOP_MATCH: (_open_questions,),
    TriggerType.PROGRESS_CLAIM: (_open_questions, _standing_records),
    TriggerType.BELIEF_CHALLENGE: (_standing_beliefs,),
    # Answered by meaning alone. A recurring feeling, a physical sensation
    # and a statement about who somebody is are not attached to anything the
    # graph can be asked for directly.
    TriggerType.PATTERN_MENTION: (),
    TriggerType.SOMATIC_MARKER: (),
    TriggerType.IDENTITY_STATEMENT: (),
}


# ---------------------------------------------------------------------------
# Talking to the graph
# ---------------------------------------------------------------------------


def _canonical_name(person_id: str, graph: ReadOnlyGraph) -> str | None:
    """The name a person's record is filed under, if the record can be read."""
    try:
        row = graph.get_node(person_id)
    except Exception:
        logger.warning(
            "could not read a person's record", exc_info=True,
            extra={"node_id": person_id},
        )
        return None
    if not row:
        return None
    name = str(row.get("canonical_name") or "").strip()
    return name or None


def _as_nodes(
    rows: list[dict[str, Any]],
    *,
    trigger: RetrievalTrigger,
    context: AnchorContext,
    anchor: StructuralAnchorType,
    value: str | None,
) -> list[RetrievedNode]:
    """Read the rows one anchor turned up into candidates."""
    return [
        to_node(
            row,
            found_by=RetrievalPass.STRUCTURAL,
            trigger_type=trigger.trigger_type,
            anchor_type=anchor,
            anchor_value=value,
            base_score=context.base_score,
        )
        for row in rows
        if has_id(row)
    ]


def _attempt(anchor: str, lookup: Callable[[], list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """
    Run one anchor lookup and let it fail on its own.

    A graph that refuses one query should cost that anchor and nothing else.
    An empty list is safe here in a way it would not be for the search as a
    whole, because the caller reports each pass separately and a turn where
    every one of them failed is reported as a turn that could not look.
    """
    try:
        return lookup()
    except Exception as exc:  # noqa: BLE001 — one broken anchor must not stop the rest
        logger.warning(
            "an anchor lookup failed and was skipped",
            extra={"anchor": anchor, "reason": type(exc).__name__},
        )
        return []


__all__ = [
    "find_by_anchors",
    "has_anchors",
    "LOOKUPS",
    "PERSON_LINKED_TYPES",
    "ERA_TAGGED_TYPES",
    "STANDING_TYPES",
    "AnchorContext",
]
