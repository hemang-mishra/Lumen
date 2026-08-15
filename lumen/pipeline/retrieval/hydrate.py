"""
Turning rows read back from the graph into candidates.

Both halves of retrieval end up here. The semantic half knows a node's id
and how close it scored and needs everything else; the structural half has
whole rows already but needs them in the same shape. One place doing the
conversion means a candidate looks the same however it was found, and the
step that merges them has nothing to special-case.

A row comes back wide — the graph returns the union of every column across
every node table, so most of what arrives is empty. Which column holds the
readable content depends on the kind of node, and that mapping is the only
real work in this file.
"""

from __future__ import annotations

import logging
from typing import Any

from lumen.schemas.enums import CandidateRetrievalSource, SignalStrength, StructuralAnchorType
from lumen.schemas.pipeline import CandidateNode

logger = logging.getLogger(__name__)

# Where each kind of node keeps the text worth showing, in the order to try.
# A pattern says what it is in its name, a belief in its statement, an
# observation in its content — the same idea under three column names.
_PREVIEW_COLUMNS: tuple[str, ...] = (
    "content",
    "pattern_name",
    "belief_statement",
    "lesson_statement",
    "event_summary",
    "session_summary",
    "chain_summary",
    "episode_summary",
    "principle_statement",
    "loop_description",
    "contradiction_summary",
    "canonical_name",
)

# How much of a node's text to carry. Enough to recognise it by; not so
# much that eight candidates crowd out the entry being reconciled.
PREVIEW_LENGTH = 240


def preview_of(row: dict[str, Any]) -> str:
    """
    Find the readable part of a node, whatever kind it is.

    Falls back to the node's own id, so a node with no recognised content
    column still produces a usable candidate rather than being silently
    dropped. Losing a real historical match because its table names things
    unusually would be a worse outcome than an ugly preview.
    """
    for column in _PREVIEW_COLUMNS:
        text = (row.get(column) or "").strip()
        if text:
            return text[:PREVIEW_LENGTH]
    return str(row.get("node_id", "unknown node"))


def signal_of(row: dict[str, Any]) -> SignalStrength:
    """
    Read how much weight a node carries, defaulting to ordinary.

    Not every kind of node records this. Treating a missing value as
    ordinary is the safe direction: it can only fail to promote something,
    never promote something that did not earn it.
    """
    try:
        return SignalStrength(row.get("signal_strength") or "STANDARD")
    except ValueError:
        return SignalStrength.STANDARD


def to_candidates(
    rows: list[dict[str, Any]],
    *,
    anchor: StructuralAnchorType,
    value: str,
) -> list[CandidateNode]:
    """
    Turn graph rows into candidates found by an anchor rather than by
    resemblance.

    Each one records which anchor led to it, so reconciliation can tell a
    node that surfaced because a name matched from one that surfaced
    because it reads similarly. Those two mean different things, and the
    second is much easier to over-trust.
    """
    return [
        CandidateNode(
            node_id=row["node_id"],
            node_type=row.get("_label", "unknown"),
            content_preview=preview_of(row),
            retrieval_source=CandidateRetrievalSource.STRUCTURAL,
            structural_anchor_type=anchor,
            structural_anchor_value=value,
        )
        for row in rows
        if row.get("node_id")
    ]


__all__ = ["preview_of", "signal_of", "to_candidates", "PREVIEW_LENGTH"]
