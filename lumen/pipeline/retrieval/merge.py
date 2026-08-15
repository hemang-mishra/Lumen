"""
Deciding which candidates actually reach reconciliation.

Both halves of retrieval can return more than the eight the next stage
accepts, and they overlap. This is where that is settled.

Two rules do all the work, and both are about what to lose rather than what
to keep.

A node found by both halves keeps its structural copy. That copy carries
which anchor led to it, which is information the other copy does not have,
and knowing that a node surfaced because a name matched — rather than
because it reads similarly — changes how much it should be trusted.

When there are too many, the resembling ones go first. That looks backwards
until you consider what each half is for. Anything the semantic half found
was found by being close, so dropping one loses a near-match. Anything the
structural half found was found precisely because closeness would never
have reached it, and dropping one loses the only route there was.
"""

from __future__ import annotations

import logging

from lumen.schemas.pipeline import CandidateNode

logger = logging.getLogger(__name__)


def merge(
    semantic: list[CandidateNode],
    structural: list[CandidateNode],
    *,
    cap: int,
) -> tuple[list[CandidateNode], list[CandidateNode]]:
    """
    Combine the two halves, remove repeats, and hold the total to the cap.

    The halves stay separate on the way out, because the next stage is told
    which one surfaced what.
    """
    kept_structural = _unique(structural)
    anchored = {candidate.node_id for candidate in kept_structural}
    kept_semantic = [
        candidate
        for candidate in _unique(semantic)
        if candidate.node_id not in anchored
    ]

    room = max(0, cap - len(kept_structural))
    if room < len(kept_semantic):
        logger.debug(
            "trimmed resembling candidates to fit the cap",
            extra={"dropped": len(kept_semantic) - room, "cap": cap},
        )
        # Already in the order the search ranked them — closeness counted
        # together with each node's own weight — so taking from the front
        # keeps the best and drops what that ranking put last.
        kept_semantic = kept_semantic[:room]

    # An anchor set alone larger than the cap is possible in principle and
    # would leave no room at all. Trimming it is the last resort, and the
    # oldest-found go first since the lookups return their own best order.
    if len(kept_structural) > cap:
        logger.warning(
            "more anchored candidates than the cap allows",
            extra={"found": len(kept_structural), "cap": cap},
        )
        kept_structural = kept_structural[:cap]

    return kept_semantic, kept_structural


def _unique(candidates: list[CandidateNode]) -> list[CandidateNode]:
    """Drop repeats, keeping the first of each and the order they arrived."""
    seen: set[str] = set()
    kept: list[CandidateNode] = []
    for candidate in candidates:
        if candidate.node_id in seen:
            continue
        seen.add(candidate.node_id)
        kept.append(candidate)
    return kept


__all__ = ["merge"]
