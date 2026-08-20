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
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from lumen.config import ScoringConfig
from lumen.graph import scoring
from lumen.graph.queries import node_type_of, tidy_row
from lumen.graph.rows import DATE_COLUMNS, happened_at, preview_of, signal_of
from lumen.query.retrieval.contracts import RetrievedNode
from lumen.schemas.enums import (
    Domain,
    RetrievalPass,
    StructuralAnchorType,
    TriggerType,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Weighting:
    """
    The clock and the settings one turn's ranking is measured against.

    Age is part of what a record is worth, so ranking needs to know what
    "now" is. Fixing it once per turn and handing it around means every
    record in one answer is aged against the same instant — otherwise a
    search running over midnight could rank two identical records
    differently for no reason anybody could see.
    """

    now: datetime
    config: ScoringConfig

    @classmethod
    def at(
        cls, now: datetime | None = None, *, config: ScoringConfig | None = None
    ) -> "Weighting":
        """A weighting for this moment, defaulting to right now."""
        return cls(
            now=now or datetime.now(timezone.utc),
            config=config or ScoringConfig(),
        )

    def weigh(self, row: dict[str, Any]) -> scoring.RecordWeights:
        """Everything that changes what this record is worth."""
        return scoring.weigh(row, now=self.now, config=self.config)


def to_node(
    row: dict[str, Any],
    *,
    weighting: Weighting,
    found_by: RetrievalPass,
    trigger_type: TriggerType | None = None,
    similarity: float | None = None,
    anchor_type: StructuralAnchorType | None = None,
    anchor_value: str | None = None,
    base_score: float | None = None,
) -> RetrievedNode:
    """
    Read one stored row into a candidate for this turn.

    The score starts from how good the match was. For a measured match that
    is the similarity; for an anchor it is the caller's base number, since
    matching a name exactly is not something that has a distance. Everything
    the record is worth then multiplies it — how strong a signal it was, how
    long since it was last true, whether the person confirmed it, and how
    often it has been the useful one.

    The parts are kept alongside the total. A ranking nobody can explain is a
    ranking nobody can fix, and once the four are multiplied together the
    reason a record placed where it did is gone.
    """
    kind = node_type_of(row)
    tidied = tidy_row(row)
    strength = signal_of(row)
    starting = similarity if similarity is not None else (base_score or 0.0)
    weights = weighting.weigh(row)

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
        rank_score=weights.applied_to(starting),
        recency_weight=weights.recency,
        trust_weight=weights.trust,
        frequency_weight=weights.frequency,
        age_band=weights.band,
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
    return happened_at(tidied)


def _first_text(tidied: dict[str, Any], columns: tuple[str, ...]) -> str | None:
    """The first of these columns that holds anything."""
    for column in columns:
        text = tidied.get(column)
        if isinstance(text, str) and text.strip():
            return text.strip()
    return None


__all__ = ["Weighting", "to_node", "has_id", "DATE_COLUMNS"]
