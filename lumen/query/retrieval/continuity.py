"""
Pass C — keeping hold of the thread of today's conversation.

The other two searches read one turn at a time. That is enough to answer a
turn and not enough to hold a conversation, because the connection worth
making is usually between two things said hours apart.

This pass is what remembers. Everything surfaced earlier today sits in a
short list, and each turn is checked against it. Anything still relevant is
offered again and counted for a little more than it would be alone: a
subject this conversation has already circled once is more likely to be the
point than something arriving cold.

Nothing here searches anything. It compares numbers already in memory — the
position this turn was searched from, and each remembered record's own
position, cached when it joined the list. That is why it costs about a
millisecond and why it can run after the other two rather than beside them:
what it needs is the measurement Pass A has just made.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from lumen.config import QueryConfig
from lumen.query.buffer import BufferEntry, SessionContextBuffer
from lumen.query.retrieval.contracts import RetrievedNode
from lumen.schemas.enums import RetrievalPass

logger = logging.getLogger(__name__)


def revisit(
    buffer: SessionContextBuffer,
    *,
    already_found: set[str],
    query_vector: Sequence[float] | None,
    keywords: Sequence[str],
    config: QueryConfig,
) -> tuple[list[RetrievedNode], dict[str, float]]:
    """
    Work out what today's conversation should carry into this turn.

    Two answers come back, because there are two different things to do.

    A remembered record the other searches did not turn up is offered again
    here, as a candidate of its own — that is the case Pass C exists for,
    where the afternoon's realisation has nothing in common, word for word,
    with the evening's question.

    A remembered record the other searches *did* turn up is not duplicated.
    It is named in the second answer instead, so the merge can lift the copy
    that already exists rather than offering the same record twice.
    """
    relevant = buffer.relevant_to(
        vector=query_vector,
        keywords=keywords,
        threshold=config.session_boost_threshold,
        keyword_threshold=config.session_boost_keyword_threshold,
    )
    if not relevant:
        return [], {}

    boosts: dict[str, float] = {}
    revisited: list[RetrievedNode] = []

    for entry, closeness in relevant:
        boosts[entry.node_id] = closeness
        if entry.node_id in already_found:
            continue
        revisited.append(_as_candidate(entry, closeness, config))

    logger.debug(
        "today's thread still applies",
        extra={
            "still_relevant": len(boosts),
            "offered_again": len(revisited),
            "held": len(buffer),
        },
    )
    return revisited, boosts


def _as_candidate(
    entry: BufferEntry, closeness: float, config: QueryConfig
) -> RetrievedNode:
    """
    Offer a remembered record as a candidate for this turn.

    It keeps the closeness it was measured at and is marked as boosted,
    because being part of today's conversation is the reason it is here at
    all — and somebody reading the list afterwards should be able to see
    that this one was carried rather than found.

    It also keeps what the record *is* — its area of life, its period, its
    date. Those are not decoration: the sensitivity gate runs again on
    whatever comes out of here, and a record arriving with no area of life
    is judged by the rule for records that have none rather than by its own.
    A record offered on one turn would then be withheld on the next, for no
    reason the person could see.
    """
    return RetrievedNode(
        node_id=entry.node_id,
        node_type=entry.node_type,
        preview=entry.preview,
        found_by=RetrievalPass.CONTINUITY,
        similarity=_clamped(closeness),
        signal_strength=entry.signal_strength,
        domain=entry.domain,
        era_tag=entry.era_tag,
        occurred_at=entry.occurred_at,
        boosted=True,
        rank_score=_clamped(closeness) * config.session_boost_multiplier,
        properties=dict(entry.properties),
    )


def to_entries(
    candidates: Sequence[RetrievedNode],
    *,
    vectors: dict[str, list[float]],
) -> list[BufferEntry]:
    """
    Turn this turn's keepers into things worth remembering tomorrow morning.

    Each one carries its position in the index where that could be read, so
    the next turn's comparison is arithmetic rather than another search. A
    record with no stored position keeps None and is compared by words
    instead — worse, and better than dropping it.
    """
    return [
        BufferEntry(
            node_id=candidate.node_id,
            node_type=candidate.node_type,
            preview=candidate.preview,
            signal_strength=candidate.signal_strength,
            domain=candidate.domain,
            era_tag=candidate.era_tag,
            occurred_at=candidate.occurred_at,
            vector=_as_tuple(vectors.get(candidate.node_id)),
            properties=dict(candidate.properties),
        )
        for candidate in candidates
    ]


def _as_tuple(vector: list[float] | None) -> tuple[float, ...] | None:
    """A stored position, frozen so nothing can edit it later."""
    return tuple(vector) if vector else None


def _clamped(score: float) -> float:
    """A closeness held inside the range the contract allows."""
    return max(0.0, min(1.0, score))


__all__ = ["revisit", "to_entries"]
