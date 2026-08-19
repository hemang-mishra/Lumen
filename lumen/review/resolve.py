"""
Turning one person's answer into the writing that was held back.

Nothing here saves anything. An answer becomes a plan — records, links, a
fresh note of what was decided, and a stamp on the note that had been
waiting — and whoever owns writing carries it out. Keeping the two apart is
what lets every rule about which answer does what be checked without a
database.

Two notes come out of this rather than one. The note that says the system
could not decide is stamped as answered and otherwise left exactly as
written; the note of what was actually done is new. Rewriting the first one
would leave a graph that reads as though the system had been sure all along,
which is the one thing it was not.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from lumen.review import cards
from lumen.review.contracts import (
    ChoiceNotOffered,
    ResolutionChoice,
    ResolutionPlan,
    StaleProposal,
)
from lumen.schemas.edges import (
    LogicalEdgeType,
    LumenEdge,
    UnsupportedEdgeError,
    resolve_edge_table,
)
from lumen.schemas.enums import (
    BookkeepingOperation,
    DecisionStatus,
    HitlResolutionChoice,
    LifecycleNodeStatus,
    ModelRole,
)
from lumen.schemas.nodes import DecisionAuditNode, RollbackPointer
from lumen.schemas.pipeline import (
    FrozenProposal,
    GraphWritePlan,
    PlannedBookkeeping,
    PlannedEdge,
    PlannedNode,
    ProposalVariant,
)

logger = logging.getLogger(__name__)

# What each answer becomes in the permanent record. Two answers map onto one
# recorded choice in both directions, because accepting a recommendation and
# picking the first of two readings are the same decision reached from
# different questions.
_RECORDED: dict[ResolutionChoice, HitlResolutionChoice] = {
    ResolutionChoice.APPROVE: HitlResolutionChoice.ACTION_A,
    ResolutionChoice.ACTION_A: HitlResolutionChoice.ACTION_A,
    ResolutionChoice.ACTION_B: HitlResolutionChoice.ACTION_B,
    ResolutionChoice.REJECT: HitlResolutionChoice.CREATE_NEW,
    ResolutionChoice.CREATE_NEW: HitlResolutionChoice.CREATE_NEW,
}

# The model named on a note nobody's model wrote. A person decided this, and
# saying so is more honest than repeating whichever model failed to.
_DECIDED_BY_PERSON = "human-review"

# Added to a waiting note's identifier to name the note of its answer. Worked
# out rather than generated, so answering the same question twice can never
# mint two notes.
_RESOLUTION_SUFFIX = "_r"


def plan_resolution(
    proposal: FrozenProposal,
    choice: ResolutionChoice,
    *,
    at: datetime,
    rows: Mapping[str, dict[str, Any]],
    recorded_choice: HitlResolutionChoice | None = None,
) -> ResolutionPlan:
    """
    Work out everything one answer comes to.

    Raises when the answer is not one this card offered, and when the record
    it would act on has since been replaced. Both are refusals rather than
    best guesses: this writes a permanent change to somebody's history, and
    the wrong one is worse than none.

    The recorded choice can be overridden for the one answer nobody gave —
    an item that ran out of time does the same thing as a rejection and must
    not be written down as though somebody chose it.
    """
    variant = _variant_for(proposal, choice)
    _refuse_if_stale(variant, rows=rows)

    audit_id = f"{proposal.audit_node_id}{_RESOLUTION_SUFFIX}"
    nodes = [saved.restored() for saved in variant.nodes]
    edges = [saved.restamped(audit_id).restored() for saved in variant.edges]
    primary_edge = _primary_edge(edges, variant)

    new_audit = _audit_note(
        proposal,
        variant,
        audit_id=audit_id,
        at=at,
        choice=recorded_choice or _RECORDED[choice],
        primary_edge=primary_edge,
    )
    link = _link_to_answer(proposal.audit_node_id, audit_id, at=at)
    stamp = PlannedBookkeeping(
        operation=BookkeepingOperation.MARK_HITL_RESOLVED,
        node_id=proposal.audit_node_id,
        at=at,
        choice=recorded_choice or _RECORDED[choice],
        resolved_action=variant.action,
    )

    write_plan = GraphWritePlan(
        nodes=[*nodes, _planned(new_audit)],
        edges=[*edges, link] if link else list(edges),
        bookkeeping=[*variant.bookkeeping, stamp],
        existing_node_ids=_already_written(proposal, variant, rows=rows),
    )

    return ResolutionPlan(
        write_plan=write_plan,
        new_audit=new_audit,
        action_taken=variant.action,
        recorded_choice=recorded_choice or _RECORDED[choice],
        writes_nothing=variant.writes_nothing,
    )


def _variant_for(
    proposal: FrozenProposal, choice: ResolutionChoice
) -> ProposalVariant:
    """
    Which saved answer a tap means.

    The list of what a card offered is asked for rather than assumed, so a
    layout and the answers it accepts cannot drift apart.
    """
    offered = cards.offered_choices(proposal)
    if choice not in offered:
        raise ChoiceNotOffered(choice.value, [item.value for item in offered])

    if choice is ResolutionChoice.ACTION_B:
        if proposal.runner_up is None:
            raise ChoiceNotOffered(choice.value, [item.value for item in offered])
        return proposal.runner_up
    if choice in (ResolutionChoice.REJECT, ResolutionChoice.CREATE_NEW):
        return proposal.fallback
    return proposal.primary


def _refuse_if_stale(
    variant: ProposalVariant, *, rows: Mapping[str, dict[str, Any]]
) -> None:
    """
    Stop when the record this answer acts on is no longer the current one.

    An answer that stands the finding on its own has no target and is
    therefore always safe to take, which is why it stays available on a card
    where everything else has gone stale.
    """
    target_id = variant.target_node_id
    if not target_id:
        return

    row = rows.get(target_id)
    if row is None:
        raise StaleProposal(target_id, "it is no longer in the graph")
    if str(row.get("status") or "") == LifecycleNodeStatus.SUPERSEDED.value:
        raise StaleProposal(
            target_id, "a newer version of it was written after this was asked"
        )


def _audit_note(
    proposal: FrozenProposal,
    variant: ProposalVariant,
    *,
    audit_id: str,
    at: datetime,
    choice: HitlResolutionChoice,
    primary_edge: PlannedEdge | None,
) -> DecisionAuditNode:
    """
    The permanent note of what was decided, and by whom.

    Carries its own way back: the link this answer created, so undoing it
    later has something to aim at, and the finding to ask about again.
    """
    handle = _edge_handle(primary_edge, audit_id)
    return DecisionAuditNode(
        node_id=audit_id,
        created_at=at,
        action=variant.action,
        source_node_id=proposal.source_node_id,
        target_node_id=variant.target_node_id,
        edge_type_created=primary_edge.table if primary_edge else None,
        edge_id=handle if primary_edge else None,
        confidence=variant.confidence,
        delta_description=variant.delta_description,
        model_used=_DECIDED_BY_PERSON,
        model_role=ModelRole.THINKING,
        hitl_resolved=True,
        hitl_resolution_timestamp=at,
        hitl_resolution_user_choice=choice,
        candidate_retrieval_source=variant.retrieval_source,
        structural_anchor_type=variant.anchor_type,
        structural_anchor_value=variant.anchor_value,
        status=DecisionStatus.ACTIVE,
        rollback_pointer=RollbackPointer(
            edge_to_invalidate=handle,
            nodes_to_requeue=[proposal.source_node_id],
        ),
    )


def _primary_edge(
    edges: list[PlannedEdge], variant: ProposalVariant
) -> PlannedEdge | None:
    """
    The link that is the point of the action, found among the ones re-stamped.

    Matched by table rather than kept by position: the saved answer names its
    own primary link, and re-stamping rebuilds every link it holds.
    """
    if variant.primary_edge_table is None:
        return None
    for edge in edges:
        if edge.table == variant.primary_edge_table:
            return edge
    return None


def _link_to_answer(
    original_id: str, audit_id: str, *, at: datetime
) -> PlannedEdge | None:
    """
    Join the note that waited to the note that answered it.

    Both directions are readable from either note afterwards, so arriving at
    a decision from anywhere in the graph shows the whole story rather than
    half of it.
    """
    try:
        table = resolve_edge_table(
            LogicalEdgeType.SUPERSEDED_BY, "DecisionAuditNode", "DecisionAuditNode"
        )
    except UnsupportedEdgeError:
        logger.warning("no table joins a decision to its answer; link not written")
        return None

    return PlannedEdge(
        logical_type=LogicalEdgeType.SUPERSEDED_BY,
        table=table,
        from_node_id=original_id,
        to_node_id=audit_id,
        edge=LumenEdge(
            source_node_id=original_id,
            target_node_id=audit_id,
            valid_from=at,
        ),
    )


def _already_written(
    proposal: FrozenProposal,
    variant: ProposalVariant,
    *,
    rows: Mapping[str, dict[str, Any]],
) -> frozenset[str]:
    """
    The records this plan may point at without creating.

    Every one of them was saved when the entry ran — the finding, whatever
    it is being matched against, the moment a change is attributed to, and
    the note that has been waiting. Gathered from the ends of the saved
    links rather than listed by hand, because an action can reach records
    the answer never mentions: attributing a changed belief to the event
    that changed it is the common case, and naming only the obvious ones
    would make the plan refuse itself.
    """
    known = {proposal.source_node_id, proposal.audit_node_id, *rows.keys()}
    if variant.target_node_id:
        known.add(variant.target_node_id)
    for update in variant.bookkeeping:
        known.add(update.node_id)
    for edge in variant.edges:
        known.add(edge.from_node_id)
        known.add(edge.to_node_id)
    return frozenset(known)


def _edge_handle(edge: PlannedEdge | None, audit_id: str) -> str:
    """
    A way back to whatever this answer created.

    An answer that created no link still needs a handle, because every note
    carries one. It records the note's own identifier, which says "there is
    nothing to invalidate here" without leaving the field empty and
    ambiguous.
    """
    if edge is None:
        return f"none:{audit_id}"
    return f"{edge.table}:{edge.from_node_id}->{edge.to_node_id}"


def _planned(audit: DecisionAuditNode) -> PlannedNode:
    """
    Wrap the new note as something the plan can carry.

    Left unsearchable on purpose. A note about a decision is machinery, and
    letting it into the search index would put the system's own bookkeeping
    in front of somebody looking for their own words.
    """
    return PlannedNode(node_type="DecisionAuditNode", node=audit)


__all__ = ["plan_resolution"]
