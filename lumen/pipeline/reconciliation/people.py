"""
Giving the people in someone's writing a record of their own.

Names are the most reliable way back into a personal history. The same
person described across a year is described completely differently every
time — the words drift, the feelings change, the vocabulary of the first
entry has nothing in common with the last. Their name does not drift.

So every person named gets one record, and everything said about them is
linked to it. That link is what lets the search find "what has been said
about Alex" without needing anything said today to resemble anything said
before.

Two things are deliberately not done here. A person already known is not
rewritten — only their mention count and the date they were last mentioned
move. And two spellings of the same person stay two records: deciding that
"my mentor" and "Alex" are one person is the same fuzzy judgement as
deciding two patterns are one, and it deserves the same care rather than
being smuggled in here.
"""

from __future__ import annotations

import logging
from datetime import datetime

from lumen.graph.provider import GraphProvider
from lumen.pipeline.reconciliation.contracts import DecisionItem, PersonSketch
from lumen.schemas.edges import LogicalEdgeType, LumenEdge, resolve_edge_table
from lumen.schemas.enums import (
    BookkeepingOperation,
    RelationshipToUser,
    SentimentTrend,
)
from lumen.schemas.ids import make_slug_node_id
from lumen.schemas.nodes import PersonEntityNode
from lumen.schemas.pipeline import PlannedBookkeeping, PlannedEdge, PlannedNode

logger = logging.getLogger(__name__)


def person_node_id(name: str) -> str:
    """
    The identifier a person's record has, worked out from their name alone.

    Deterministic on purpose: it means asking whether someone is already
    known is a single lookup, with no search and no matching involved.
    """
    return make_slug_node_id("person", name)


def resolve_people(
    items: list[DecisionItem],
    sketches: list[PersonSketch],
    *,
    graph: GraphProvider,
    at: datetime,
) -> tuple[list[PlannedNode], list[PlannedEdge], list[PlannedBookkeeping]]:
    """
    Work out the person records and links this entry calls for.

    Runs once for the whole entry rather than once per finding, since
    several findings routinely name the same person and each would otherwise
    repeat the same lookup and plan the same record twice.

    Nothing is saved here. What comes back is a list of records to create, a
    list of links between findings and people, and the small updates for
    people already known.
    """
    nodes: list[PlannedNode] = []
    edges: list[PlannedEdge] = []
    bookkeeping: list[PlannedBookkeeping] = []
    described = {sketch.name.strip().lower(): sketch for sketch in sketches}
    planned: set[str] = set()

    for name, mentioned_by in _mentions(items).items():
        node_id = person_node_id(name)

        if node_id not in planned:
            planned.add(node_id)
            if _already_known(graph, node_id):
                bookkeeping.append(
                    PlannedBookkeeping(
                        operation=BookkeepingOperation.TOUCH_PERSON,
                        node_id=node_id,
                        at=at,
                    )
                )
            else:
                nodes.append(
                    _new_person(
                        name, described.get(name.lower()), node_id=node_id, at=at
                    )
                )

        edges.extend(_mention_edges(node_id, mentioned_by, at=at))

    return nodes, edges, bookkeeping


def _mentions(items: list[DecisionItem]) -> dict[str, list[DecisionItem]]:
    """
    Group the findings by the person each one names.

    The first spelling seen wins for the rest of the entry, so two findings
    writing the same name differently still reach one record.
    """
    grouped: dict[str, list[DecisionItem]] = {}
    canonical: dict[str, str] = {}

    for item in items:
        for raw in item.person_refs:
            name = raw.strip()
            if not name:
                continue
            key = name.lower()
            settled = canonical.setdefault(key, name)
            grouped.setdefault(settled, []).append(item)

    return grouped


def _already_known(graph: GraphProvider, node_id: str) -> bool:
    """
    Whether this person already has a record.

    A graph that cannot answer is treated as not knowing them. That risks
    planning a record that already exists, which fails loudly while saving —
    far better than skipping a record that does not, which would leave every
    link to that person dangling.
    """
    try:
        return graph.get_node(node_id) is not None
    except Exception:
        logger.warning("could not check for an existing person record", extra={"node_id": node_id})
        return False


def _new_person(
    name: str, sketch: PersonSketch | None, *, node_id: str, at: datetime
) -> PlannedNode:
    """
    Build the record for someone appearing for the first time.

    How they relate to the writer is only recorded when the entry actually
    says. Guessing at it from one mention is how a colleague becomes a
    friend in somebody's permanent history.
    """
    return PlannedNode(
        node_type="PersonEntityNode",
        node=PersonEntityNode(
            node_id=node_id,
            canonical_name=name,
            first_mentioned_at=at,
            last_mentioned_at=at,
            mention_count=1,
            relationship_to_user=_read_relationship(sketch),
            relationship_sentiment_trend=_read_sentiment(sketch),
            aliases=[name],
        ),
    )


def _mention_edges(
    node_id: str, mentioned_by: list[DecisionItem], *, at: datetime
) -> list[PlannedEdge]:
    """
    Link every finding that named a person to that person's record.

    A finding whose kind has no link to a person is skipped with a note
    rather than failing the entry — the person's record still gets created,
    and everything else about them is still connected.
    """
    edges: list[PlannedEdge] = []
    seen: set[str] = set()

    for item in mentioned_by:
        if item.node_id in seen:
            continue
        seen.add(item.node_id)
        try:
            table = resolve_edge_table(
                LogicalEdgeType.MENTIONS, item.node_type, "PersonEntityNode"
            )
        except ValueError:
            logger.debug("no link from %s to a person; skipping", item.node_type)
            continue

        edges.append(
            PlannedEdge(
                logical_type=LogicalEdgeType.MENTIONS,
                table=table,
                from_node_id=item.node_id,
                to_node_id=node_id,
                edge=LumenEdge(
                    source_node_id=item.node_id,
                    target_node_id=node_id,
                    valid_from=at,
                ),
            )
        )
    return edges


def _read_relationship(sketch: PersonSketch | None) -> RelationshipToUser:
    """How this person relates to the writer, where the entry said."""
    if sketch is None:
        return RelationshipToUser.UNKNOWN
    try:
        return RelationshipToUser(sketch.relationship.strip().upper())
    except (ValueError, AttributeError):
        return RelationshipToUser.UNKNOWN


def _read_sentiment(sketch: PersonSketch | None) -> SentimentTrend:
    """How the writer seems to feel about this person, where the entry said."""
    if sketch is None:
        return SentimentTrend.UNKNOWN
    try:
        return SentimentTrend(sketch.sentiment.strip().upper())
    except (ValueError, AttributeError):
        return SentimentTrend.UNKNOWN


__all__ = ["person_node_id", "resolve_people"]
