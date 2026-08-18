"""
Turning a stored row into something a conversation can use.

All three searches end here. One finds records by meaning, one by what they
are attached to, one by remembering what was already said today — and after
that it stops mattering which, because what the next stage wants is the same
shape either way.

The work is small and worth doing in one place: the graph answers with the
union of every column across every kind of record, so reading one means
knowing which column holds its text, which holds its weight, and which of
the many date columns is the one that says when the thing happened.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from lumen.graph.queries import node_type_of, tidy_row
from lumen.graph.rows import SIGNAL_WEIGHT, preview_of, signal_of
from lumen.query.retrieval.contracts import RetrievedNode
from lumen.schemas.enums import (
    Domain,
    RetrievalPass,
    StructuralAnchorType,
    TriggerType,
)

logger = logging.getLogger(__name__)

# Where a record says when it happened, in the order to try. Not every kind
# has all three: a standing belief has no single moment it occurred, so the
# date it became true is the closest honest answer.
DATE_COLUMNS: tuple[str, ...] = ("occurred_at", "valid_from", "created_at")


def to_node(
    row: dict[str, Any],
    *,
    found_by: RetrievalPass,
    trigger_type: TriggerType | None = None,
    similarity: float | None = None,
    anchor_type: StructuralAnchorType | None = None,
    anchor_value: str | None = None,
    base_score: float | None = None,
) -> RetrievedNode:
    """
    Read one stored row into a candidate for this turn.

    The score is the record's own weight applied to how good the match was.
    For a measured match that is the similarity; for an anchor it is the
    caller's base number, since matching a name exactly is not something
    that has a distance. Either way the weight multiplies it, so a
    life-defining realisation outranks a passing note that happens to be
    worded alike.
    """
    kind = node_type_of(row)
    tidied = tidy_row(row)
    strength = signal_of(row)
    starting = similarity if similarity is not None else (base_score or 0.0)

    return RetrievedNode(
        node_id=str(row.get("node_id") or ""),
        node_type=kind,
        preview=preview_of(row),
        found_by=found_by,
        trigger_type=trigger_type,
        similarity=similarity,
        signal_strength=strength,
        domain=_domain_of(tidied),
        era_tag=_first_text(tidied, ("era_tag", "historical_era")),
        occurred_at=_happened_at(tidied),
        anchor_type=anchor_type,
        anchor_value=anchor_value,
        rank_score=starting * SIGNAL_WEIGHT[strength],
        properties=tidied,
    )


def has_id(row: dict[str, Any]) -> bool:
    """Whether a row is usable at all. A record with no identifier is not."""
    return bool(row.get("node_id"))


def _domain_of(tidied: dict[str, Any]) -> Domain | None:
    """
    The area of life a record belongs to, where it records one.

    Only the standing records do — patterns, beliefs, lessons, principles.
    An individual observation records no area, and that absence is a fact
    the sensitivity gate has to reason about rather than paper over.
    """
    raw = tidied.get("domain")
    if not raw:
        return None
    try:
        return Domain(str(raw))
    except ValueError:
        logger.debug("a record names an area of life nothing else knows: %s", raw)
        return None


def _happened_at(tidied: dict[str, Any]) -> datetime | None:
    """When a record says it happened, or nothing if it says nothing readable."""
    for column in DATE_COLUMNS:
        parsed = _as_datetime(tidied.get(column))
        if parsed is not None:
            return parsed
    return None


def _as_datetime(value: Any) -> datetime | None:
    """
    Read a stored date back.

    Dates live in text columns, so what comes back is whatever was written.
    A value that will not parse is treated as no date at all: an unreadable
    timestamp should cost the ordering a little, not fail the turn.
    """
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _first_text(tidied: dict[str, Any], columns: tuple[str, ...]) -> str | None:
    """The first of these columns that holds anything."""
    for column in columns:
        text = tidied.get(column)
        if isinstance(text, str) and text.strip():
            return text.strip()
    return None


__all__ = ["to_node", "has_id", "DATE_COLUMNS"]
