"""
Turning rows read back from the graph into candidates.

Both halves of retrieval end up here. The semantic half knows a node's id
and how close it scored and needs everything else; the structural half has
whole rows already but needs them in the same shape. One place doing the
conversion means a candidate looks the same however it was found, and the
step that merges them has nothing to special-case.

A row comes back wide — the graph returns the union of every column across
every node table, so most of what arrives is empty. Which column holds the
readable content depends on the kind of node; that mapping now lives in
`lumen.graph.rows`, because the live conversation layer reads rows the same
way and two copies of it would eventually disagree.
"""

from __future__ import annotations

import logging
from typing import Any

from lumen.graph.rows import PREVIEW_LENGTH, preview_of, signal_of
from lumen.schemas.enums import CandidateRetrievalSource, StructuralAnchorType
from lumen.schemas.pipeline import CandidateNode

logger = logging.getLogger(__name__)


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
