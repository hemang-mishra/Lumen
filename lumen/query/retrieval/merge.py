"""
Settling three answers into one list.

The searches overlap on purpose, so the same record arrives more than once
and something has to decide which copy survives and in what order the
survivors go.

Two rules do all the work, and both are about what to lose.

A record found by an anchor keeps its anchor copy. That copy knows *why* it
was found — this person's name, this period of their life — and knowing that
changes how much it should be trusted. The resembling copy knows only that
the words were close, which is the easier thing to over-trust.

When there are too many, the resembling ones go first. That reads backwards
until you consider what each search is for. Anything found by resemblance
was found by being close, so dropping one loses a near-match. Anything found
by an anchor was found precisely because closeness would never have reached
it, so dropping one loses the only route there was.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence

from lumen.query.retrieval.contracts import RetrievedNode
from lumen.schemas.enums import RetrievalPass

logger = logging.getLogger(__name__)

# Which copy of a record wins when two searches both found it. Lower is
# better, and the order is the order of how much each copy knows about why
# it is here.
PRECEDENCE: dict[RetrievalPass, int] = {
    RetrievalPass.STRUCTURAL: 0,
    RetrievalPass.SEMANTIC: 1,
    RetrievalPass.CONTINUITY: 2,
}


def merge(
    *groups: Sequence[RetrievedNode],
    boosts: Mapping[str, float] | None = None,
    boost_multiplier: float = 1.0,
    cap: int,
) -> list[RetrievedNode]:
    """
    Combine what the searches found, remove repeats, and cut to the cap.

    Records named in `boosts` were part of today's conversation already, and
    their score is multiplied — the surviving copy is marked as boosted so
    that fact stays visible rather than being buried in a number.
    """
    best: dict[str, RetrievedNode] = {}

    for group in groups:
        for candidate in group:
            held = best.get(candidate.node_id)
            if held is None or _wins(candidate, held):
                best[candidate.node_id] = candidate

    lifted = [_apply_boost(node, boosts or {}, boost_multiplier) for node in best.values()]
    ranked = sorted(
        lifted,
        key=lambda node: (node.rank_score, PRECEDENCE[node.found_by] * -1),
        reverse=True,
    )

    room = max(int(cap), 0)
    if len(ranked) > room:
        logger.debug(
            "more history was found than one turn can carry",
            extra={"found": len(ranked), "cap": room},
        )
    return ranked[:room]


def _wins(candidate: RetrievedNode, held: RetrievedNode) -> bool:
    """
    Whether a newly-seen copy of a record should replace the one held.

    Which search found it decides first, because that is a difference in
    what the copy knows. Between two copies from the same search, the better
    match wins.
    """
    mine = PRECEDENCE[candidate.found_by]
    theirs = PRECEDENCE[held.found_by]
    if mine != theirs:
        return mine < theirs
    return candidate.rank_score > held.rank_score


def _apply_boost(
    node: RetrievedNode, boosts: Mapping[str, float], multiplier: float
) -> RetrievedNode:
    """Lift a record that today's conversation has already been round once."""
    if node.node_id not in boosts or node.boosted:
        return node
    return node.model_copy(
        update={"boosted": True, "rank_score": node.rank_score * multiplier}
    )


__all__ = ["merge", "PRECEDENCE"]
