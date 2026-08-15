"""
Turning settled decisions into the records and links they imply.

Nothing here saves anything. Each decision is translated into exactly what
it means for the graph — a link, a new record, a next version, a note of the
decision itself — and the whole lot is handed on to whoever owns writing.
Keeping the two apart is what makes it possible to test every consequence of
every action without a database, and it means the code that writes has no
judgement in it at all.

There is one builder per action and a table pointing to them, so adding a
ninth action later means writing a builder rather than editing a chain of
branches. Every one of them, including the two that write nothing, goes
through the same wrapper that records the decision permanently. An action
cannot forget its own audit note, because it is not the thing that writes
it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from lumen.pipeline.reconciliation import promote
from lumen.pipeline.reconciliation.contracts import HistoricalNode, SettledDecision
from lumen.schemas.edges import (
    DialecticEdge,
    EvolvedFromEdge,
    LogicalEdgeType,
    LumenEdge,
    ReconciliationEdge,
    RegulatesEdge,
    UnsupportedEdgeError,
    resolve_edge_table,
)
from lumen.schemas.enums import (
    BookkeepingOperation,
    DecisionStatus,
    ReconciliationAction,
)
from lumen.schemas.ids import make_node_id
from lumen.schemas.nodes import ContradictionNode, DecisionAuditNode, RollbackPointer
from lumen.schemas.pipeline import PlannedBookkeeping, PlannedEdge, PlannedNode

logger = logging.getLogger(__name__)


class PlanFragment(BaseModel):
    """
    Everything one decision adds to the plan.

    Attributes:
        nodes: Records to create, in the order they must be created.
        edges: Links to create.
        bookkeeping: Small changes to records that already exist.
    """

    model_config = ConfigDict(extra="forbid")

    nodes: list[PlannedNode] = []
    edges: list[PlannedEdge] = []
    bookkeeping: list[PlannedBookkeeping] = []

    def merged_with(self, other: "PlanFragment") -> "PlanFragment":
        """Join two fragments, keeping the order of both."""
        return PlanFragment(
            nodes=[*self.nodes, *other.nodes],
            edges=[*self.edges, *other.edges],
            bookkeeping=[*self.bookkeeping, *other.bookkeeping],
        )


@dataclass
class PlanContext:
    """
    What building a plan needs to know beyond the decision itself.

    Attributes:
        at: The moment every new record and link is stamped with.
        event_date: The day the writing belongs to, used in identifiers so
            the graph stays readable by eye.
        history: The existing records involved, read back in full. A next
            version is built from the whole of the record it follows, so a
            preview is not enough.
        exists: Whether an identifier is already taken.
        anchor_node_id: What a change can be attributed to — something that
            happened, or the session the thinking happened in.
        anchor_node_type: Which of the two that is.
    """

    at: datetime
    event_date: date
    history: dict[str, HistoricalNode] = field(default_factory=dict)
    exists: Callable[[str], bool] = lambda _node_id: False
    anchor_node_id: str | None = None
    anchor_node_type: str | None = None


# What one action's own records and links come to, before the decision note
# is added. Kept separate so the note can record which link was the point of
# the action, which is what reversing it later needs.
@dataclass
class _ActionWrites:
    fragment: PlanFragment = field(default_factory=PlanFragment)
    primary_edge: PlannedEdge | None = None


def plan_for(
    decision: SettledDecision, context: PlanContext, *, sequence: int
) -> tuple[PlanFragment, DecisionAuditNode]:
    """
    Work out everything one decision comes to, and the note recording it.

    A decision being held back for a person still produces a note. That is
    deliberate: an entry where nothing happened and an entry where something
    was deliberately not done look identical in a graph, and only one of
    them is waiting on somebody.
    """
    audit_id = make_node_id("d", context.event_date, sequence)

    writes = (
        _ActionWrites()
        if decision.is_refused
        else _BUILDERS.get(decision.action, _nothing)(decision, context, audit_id)
    )

    audit = _audit_note(decision, context, audit_id, writes.primary_edge)
    fragment = writes.fragment.merged_with(
        PlanFragment(
            nodes=[PlannedNode(node_type="DecisionAuditNode", node=audit)],
            edges=_decided_by(decision, audit_id, context),
        )
    )
    return fragment, audit


# ---------------------------------------------------------------------------
# One builder per action
# ---------------------------------------------------------------------------


def _merge(
    decision: SettledDecision, context: PlanContext, audit_id: str
) -> _ActionWrites:
    """
    Link a finding to the record it says the same thing as.

    Nothing is collapsed and nothing is deleted. Both records go on
    existing with their own history, and the link between them is the whole
    of what merging means here. Undoing it later is a matter of marking the
    link invalid, with both sides untouched.
    """
    edge = _link(decision, LogicalEdgeType.SAME_AS, audit_id, context)
    return _ActionWrites(PlanFragment(edges=[edge] if edge else []), edge)


def _reinforce(
    decision: SettledDecision, context: PlanContext, audit_id: str
) -> _ActionWrites:
    """
    Add one more piece of evidence to something already known.

    The finding stays its own separate occasion — the point of reinforcing
    is that this happened again, not that it is the same happening. The
    record it supports has its evidence count and last-seen date moved,
    which is what lets a well-supported pattern outrank a passing thought.
    """
    edge = _link(decision, LogicalEdgeType.REINFORCES, audit_id, context)
    bookkeeping = (
        [
            PlannedBookkeeping(
                operation=BookkeepingOperation.RECORD_REINFORCEMENT,
                node_id=decision.target_node_id or "",
                at=context.at,
            )
        ]
        if edge
        else []
    )
    return _ActionWrites(
        PlanFragment(edges=[edge] if edge else [], bookkeeping=bookkeeping), edge
    )


def _evolve(
    decision: SettledDecision, context: PlanContext, audit_id: str
) -> _ActionWrites:
    """
    Write the next version of something that has genuinely changed.

    The old version is never touched. It keeps its wording, its dates and
    its place in the graph forever, and is only marked as no longer current.
    The new version points back at it, and at whatever caused the change —
    an event, or the session in which the person worked it out. Without that
    second link a version chain is a list of edits; with it, it is a story
    that can be read years later.
    """
    existing = context.history.get(decision.target_node_id or "")
    if existing is None or not existing.row:
        logger.warning(
            "cannot build a new version without the old one",
            extra={"target_node_id": decision.target_node_id},
        )
        return _ActionWrites()

    new_version = promote.next_version(
        existing.row,
        statement=_new_wording(decision),
        delta=decision.delta_description or "",
        at=context.at,
        took_ownership=decision.co_created_origin,
    )

    evolved_from = _edge_between(
        LogicalEdgeType.EVOLVED_FROM,
        from_node_id=new_version.node.node_id,
        from_type=new_version.node_type,
        to_node_id=existing.node_id,
        to_type=existing.node_type,
        edge=EvolvedFromEdge(
            source_node_id=new_version.node.node_id,
            target_node_id=existing.node_id,
            valid_from=context.at,
            decision_id=audit_id,
            confidence=decision.confidence,
        ),
    )
    caused_by = _causal_anchor(new_version, decision, context, audit_id)

    edges = [edge for edge in (evolved_from, caused_by) if edge is not None]
    return _ActionWrites(
        PlanFragment(
            nodes=[new_version],
            edges=edges,
            bookkeeping=[
                PlannedBookkeeping(
                    operation=BookkeepingOperation.MARK_SUPERSEDED,
                    node_id=existing.node_id,
                    at=context.at,
                )
            ],
        ),
        evolved_from,
    )


def _branch(
    decision: SettledDecision, context: PlanContext, audit_id: str
) -> _ActionWrites:
    """
    Record something as its own thing.

    Two outcomes, and both are correct. A finding that makes a claim about
    how this person works becomes a lasting record of its own. A finding
    that belongs to its day — what happened, how it felt — becomes nothing
    more than what it already is, saved with the entry and linked to it. The
    second is much the commoner, and treating it as a failure is how a
    graph ends up with a permanent record for every sentence.
    """
    loop = promote.build_open_loop(
        decision.item, at=context.at, exists=context.exists
    )
    if loop is not None:
        return _ActionWrites(PlanFragment(nodes=[loop], edges=_investigates(loop, decision, context)))

    standing = promote.build_standing_node(
        decision.item, decision.new_node, at=context.at, exists=context.exists
    )
    if standing is None:
        logger.debug(
            "finding stays with its entry rather than becoming a lasting record",
            extra={"node_id": decision.item.node_id},
        )
        return _ActionWrites()

    edge = _edge_between(
        LogicalEdgeType.BRANCHES_TO,
        from_node_id=decision.item.node_id,
        from_type=decision.item.node_type,
        to_node_id=standing.node.node_id,
        to_type=standing.node_type,
        edge=ReconciliationEdge(
            source_node_id=decision.item.node_id,
            target_node_id=standing.node.node_id,
            valid_from=context.at,
            decision_id=audit_id,
            confidence=decision.confidence,
        ),
    )
    return _ActionWrites(
        PlanFragment(nodes=[standing], edges=[edge] if edge else []), edge
    )


def _contradict(
    decision: SettledDecision, context: PlanContext, audit_id: str
) -> _ActionWrites:
    """
    Record two beliefs the person holds at once that cannot both be true.

    Neither belief gives way. That is the whole point: people do hold
    incompatible things, and forcing a resolution the person has not reached
    would be the system deciding something about them that they have not
    decided about themselves. A record joining the two makes the clash
    visible and leaves it standing until they resolve it.

    The older belief is left exactly as written. The clash is fully
    described by the joining record and the links either side of it, so
    editing a belief already in their history to mention it would be an
    edit for no gain.
    """
    target = decision.target_node_id
    if target is None:
        return _ActionWrites()

    contradiction_id = f"con_{audit_id.removeprefix('d_')}"
    new_belief = promote.build_contradicting_belief(
        decision.item,
        decision.new_node,
        contradiction_node_id=contradiction_id,
        at=context.at,
        exists=context.exists,
    )
    contradiction = PlannedNode(
        node_type="ContradictionNode",
        node=ContradictionNode(
            node_id=contradiction_id,
            created_at=context.at,
            valid_from=context.at,
            belief_a_id=target,
            belief_b_id=new_belief.node.node_id,
            contradiction_summary=decision.contradiction_summary or "",
            decision_id=audit_id,
        ),
    )

    links = [
        _edge_between(
            LogicalEdgeType.CONTRADICTS,
            from_node_id=contradiction_id,
            from_type="ContradictionNode",
            to_node_id=belief_id,
            to_type="BeliefNode",
            edge=ReconciliationEdge(
                source_node_id=contradiction_id,
                target_node_id=belief_id,
                valid_from=context.at,
                decision_id=audit_id,
                confidence=decision.confidence,
            ),
        )
        for belief_id in (target, new_belief.node.node_id)
    ]
    edges = [edge for edge in links if edge is not None]

    return _ActionWrites(
        PlanFragment(nodes=[new_belief, contradiction], edges=edges),
        edges[0] if edges else None,
    )


def _dialectic(
    decision: SettledDecision, context: PlanContext, audit_id: str
) -> _ActionWrites:
    """
    Record two things that oppose each other and are both true.

    Different from a contradiction, and the difference matters. A
    contradiction is a clash waiting to be resolved; a tension is a shape
    someone's life has. "Criticism helps me" and "I need to feel
    appreciated" are both true of the same person at the same time, and
    resolving either one away would lose something real. The link carries a
    sentence saying what the tension is, because the link means nothing
    without it.
    """
    standing = promote.build_standing_node(
        decision.item, decision.new_node, at=context.at, exists=context.exists
    )
    if standing is None or decision.target_node_id is None:
        return _ActionWrites()

    edge = _edge_between(
        LogicalEdgeType.DIALECTIC,
        from_node_id=standing.node.node_id,
        from_type=standing.node_type,
        to_node_id=decision.target_node_id,
        to_type=decision.target_type or "",
        edge=DialecticEdge(
            source_node_id=standing.node.node_id,
            target_node_id=decision.target_node_id,
            valid_from=context.at,
            decision_id=audit_id,
            confidence=decision.confidence,
            tension_summary=decision.tension_summary or "",
        ),
    )
    return _ActionWrites(
        PlanFragment(nodes=[standing], edges=[edge] if edge else []), edge
    )


def _regulate(
    decision: SettledDecision, context: PlanContext, audit_id: str
) -> _ActionWrites:
    """
    Record someone catching themselves in the middle of a known habit.

    Noticing a habit and interrupting it is not the same as no longer having
    it, and recording it as a change would be flattering and wrong. The
    pattern stays exactly as it is; what is added is evidence that the
    person is now watching it, which is the thing that actually changes
    first.
    """
    edge = _link(decision, LogicalEdgeType.REGULATES, audit_id, context)
    return _ActionWrites(PlanFragment(edges=[edge] if edge else []), edge)


def _nothing(
    decision: SettledDecision, context: PlanContext, audit_id: str
) -> _ActionWrites:
    """No records and no links — used where the decision is to wait."""
    return _ActionWrites()


# The table that replaces a chain of branches. A ninth action would be a new
# entry here and a new builder, and nothing else in this file would move.
_BUILDERS: dict[
    ReconciliationAction,
    Callable[[SettledDecision, PlanContext, str], _ActionWrites],
] = {
    ReconciliationAction.MERGE: _merge,
    ReconciliationAction.REINFORCE: _reinforce,
    ReconciliationAction.EVOLVE: _evolve,
    ReconciliationAction.BRANCH: _branch,
    ReconciliationAction.CONTRADICT: _contradict,
    ReconciliationAction.DIALECTIC: _dialectic,
    ReconciliationAction.REGULATE: _regulate,
    ReconciliationAction.AMBIGUOUS: _nothing,
}


# ---------------------------------------------------------------------------
# The parts every action shares
# ---------------------------------------------------------------------------


def _audit_note(
    decision: SettledDecision,
    context: PlanContext,
    audit_id: str,
    primary_edge: PlannedEdge | None,
) -> DecisionAuditNode:
    """
    Write the permanent note of one decision.

    Every action produces one, including the ones that decided to wait.
    Without it a graph can be read but not questioned: you can see that two
    ideas were joined and never learn why, how sure anything was, what the
    alternative had been, or how to undo it.
    """
    status = _status_for(decision)
    return DecisionAuditNode(
        node_id=audit_id,
        created_at=context.at,
        action=decision.action,
        source_node_id=decision.item.node_id,
        target_node_id=decision.target_node_id,
        edge_type_created=primary_edge.table if primary_edge else None,
        edge_id=_edge_handle(primary_edge),
        confidence=decision.confidence,
        confidence_runner_up=decision.runner_up_confidence,
        runner_up_action=decision.runner_up_action,
        delta_description=decision.delta_description,
        model_used=decision.model_used,
        model_role=decision.model_role,
        candidate_retrieval_source=decision.retrieval_source,
        structural_anchor_type=decision.anchor_type,
        structural_anchor_value=decision.anchor_value,
        co_created_origin=decision.co_created_origin,
        status=status,
        rollback_pointer=RollbackPointer(
            edge_to_invalidate=_edge_handle(primary_edge) or f"none:{audit_id}",
            nodes_to_requeue=[decision.item.node_id],
        ),
    )


def _status_for(decision: SettledDecision) -> DecisionStatus:
    """
    Where a decision stands.

    A tie is its own state because it never had a preferred answer to hold
    back — everything else that is waiting had one clear reading that simply
    was not sure enough, or could not be acted on.

    The tie is recognised by its action rather than by the check that caught
    it. A tie never acts on its own by rule, so it has to reach that state
    however it arrived here, not only when it came by the usual route.
    """
    if decision.action is ReconciliationAction.AMBIGUOUS:
        return DecisionStatus.PENDING_HITL
    if not decision.is_refused:
        return DecisionStatus.ACTIVE
    return DecisionStatus.BELOW_THRESHOLD


def _edge_handle(edge: PlannedEdge | None) -> str | None:
    """
    A readable handle for the link a decision created.

    Links have no identifier of their own in the graph, so reversing a
    decision finds them by the decision that made them. This handle says
    which link that was, in a form a person reading the note can follow.
    """
    if edge is None:
        return None
    return f"{edge.table}:{edge.from_node_id}->{edge.to_node_id}"


def _decided_by(
    decision: SettledDecision, audit_id: str, context: PlanContext
) -> list[PlannedEdge]:
    """Link a finding to the note of the decision made about it."""
    edge = _edge_between(
        LogicalEdgeType.DECIDED_BY,
        from_node_id=decision.item.node_id,
        from_type=decision.item.node_type,
        to_node_id=audit_id,
        to_type="DecisionAuditNode",
        edge=LumenEdge(
            source_node_id=decision.item.node_id,
            target_node_id=audit_id,
            valid_from=context.at,
        ),
    )
    return [edge] if edge else []


def _link(
    decision: SettledDecision,
    logical: LogicalEdgeType,
    audit_id: str,
    context: PlanContext,
) -> PlannedEdge | None:
    """Build the straightforward case: one link from the finding to its target."""
    if decision.target_node_id is None or decision.target_type is None:
        return None

    edge: LumenEdge
    if logical is LogicalEdgeType.REGULATES:
        edge = RegulatesEdge(
            source_node_id=decision.item.node_id,
            target_node_id=decision.target_node_id,
            valid_from=context.at,
            decision_id=audit_id,
            confidence=decision.confidence,
            regulation_summary=decision.regulation_summary or "",
        )
    else:
        edge = ReconciliationEdge(
            source_node_id=decision.item.node_id,
            target_node_id=decision.target_node_id,
            valid_from=context.at,
            decision_id=audit_id,
            confidence=decision.confidence,
        )

    return _edge_between(
        logical,
        from_node_id=decision.item.node_id,
        from_type=decision.item.node_type,
        to_node_id=decision.target_node_id,
        to_type=decision.target_type,
        edge=edge,
    )


def _causal_anchor(
    new_version: PlannedNode,
    decision: SettledDecision,
    context: PlanContext,
    audit_id: str,
) -> PlannedEdge | None:
    """
    Link a change to whatever could have caused it.

    Every reflective entry produces something to point at, so this is
    normally just a matter of using it. Where there is nothing, the decision
    was already refused before reaching here.
    """
    if context.anchor_node_id is None or context.anchor_node_type is None:
        return None
    return _edge_between(
        LogicalEdgeType.CAUSED_BY,
        from_node_id=new_version.node.node_id,
        from_type=new_version.node_type,
        to_node_id=context.anchor_node_id,
        to_type=context.anchor_node_type,
        edge=ReconciliationEdge(
            source_node_id=new_version.node.node_id,
            target_node_id=context.anchor_node_id,
            valid_from=context.at,
            decision_id=audit_id,
            confidence=decision.confidence,
        ),
    )


def _investigates(
    loop: PlannedNode, decision: SettledDecision, context: PlanContext
) -> list[PlannedEdge]:
    """Link a standing question to the entry that raised it again."""
    edge = _edge_between(
        LogicalEdgeType.INVESTIGATED_BY,
        from_node_id=loop.node.node_id,
        from_type="OpenLoopNode",
        to_node_id=decision.item.episode_id,
        to_type="EpisodeNode",
        edge=LumenEdge(
            source_node_id=loop.node.node_id,
            target_node_id=decision.item.episode_id,
            valid_from=context.at,
        ),
    )
    return [edge] if edge else []


def _edge_between(
    logical: LogicalEdgeType,
    *,
    from_node_id: str,
    from_type: str,
    to_node_id: str,
    to_type: str,
    edge: LumenEdge,
) -> PlannedEdge | None:
    """
    Work out which table a link belongs in, and refuse if there is none.

    Resolving this while planning rather than while saving is the point: an
    unsupported combination becomes a decision that quietly did less, not an
    entry that stops saving halfway through with some of it already written.
    """
    try:
        table = resolve_edge_table(logical, from_type, to_type)
    except UnsupportedEdgeError:
        logger.warning(
            "no link of this kind exists between these records",
            extra={"logical_type": logical.value, "from": from_type, "to": to_type},
        )
        return None

    return PlannedEdge(
        logical_type=logical,
        table=table,
        from_node_id=from_node_id,
        to_node_id=to_node_id,
        edge=edge,
    )


def _new_wording(decision: SettledDecision) -> str:
    """The wording a new version takes, falling back to the finding's own words."""
    if decision.new_node is not None and decision.new_node.statement.strip():
        return decision.new_node.statement.strip()
    return decision.item.text


__all__ = ["PlanFragment", "PlanContext", "plan_for"]
