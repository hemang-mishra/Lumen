"""
Keeping a decision that was held back, so it can be carried out later.

When the system cannot settle something it writes a note saying so and
stops. That note records what it was leaning towards and how sure it was,
which is enough to describe the question and nowhere near enough to answer
it: the new wording for a belief that has moved on, or the shape of a record
that does not exist yet, is worked out while deciding and then thrown away.

So it is kept. At the moment of giving up, every answer the question could
have is built in full — the actual records, links and updates — and saved.
Answering it a week later replays what was saved. Nothing is asked of a
model, nobody waits, and what lands is exactly what the person was shown.

Nothing here writes anything or reads a database. It takes a decision and
returns the saved form of it.

It lives beside the code that makes decisions rather than beside the code
that shows them, because the thing being kept is what *this* stage was about
to do. The review queue only ever reads it back.
"""

from __future__ import annotations

import logging

from lumen.pipeline.reconciliation import plan
from lumen.pipeline.reconciliation.contracts import SettledDecision
from lumen.schemas.enums import HitlEntryType, ReconciliationAction
from lumen.schemas.pipeline import (
    FrozenProposal,
    ProposalVariant,
    SavedEdge,
    SavedNode,
)

logger = logging.getLogger(__name__)


# The answer to "no, this is something else", and the answer an unanswered
# item eventually settles on. Always built, whatever the recommendation was.
_FALLBACK_ACTION = ReconciliationAction.BRANCH


def freeze(
    decision: SettledDecision,
    context: plan.PlanContext,
    *,
    audit_id: str,
    entry_type: HitlEntryType,
) -> FrozenProposal:
    """
    Save every answer a held-back decision could be given.

    Three at most: what the system recommended, the reading it nearly chose
    instead, and recording the finding as its own separate thing. A card
    offers whichever of them make sense for the kind of question it is.

    The second reading is only saved when there is one to save. A tie has
    one by definition; a decision that was simply not confident enough
    usually does not.
    """
    primary = _variant(_leading_reading(decision), context, audit_id=audit_id)
    runner_up = _runner_up_variant(decision, context, audit_id=audit_id)
    fallback = _variant(
        _as_fallback(decision), context, audit_id=audit_id, is_fallback=True
    )

    return FrozenProposal(
        audit_node_id=audit_id,
        entry_type=entry_type,
        source_node_id=decision.item.node_id,
        source_type=decision.item.node_type,
        source_text=decision.item.text,
        episode_id=decision.item.episode_id,
        event_date=context.event_date,
        episode_index=context.episode_index,
        frozen_at=context.at,
        primary=primary,
        runner_up=runner_up,
        fallback=fallback,
    )


def _leading_reading(decision: SettledDecision) -> SettledDecision:
    """
    The reading that was in front, with its own label back on.

    A tie is relabelled as having no preference, which is right for the
    permanent note and wrong for the card: "take the first reading" has to
    know what the first reading was. Anything that is not a tie is already
    itself.
    """
    if decision.tied_action is None:
        return decision
    return decision.model_copy(
        update={
            "action": decision.tied_action,
            "confidence": decision.tied_confidence or 0.0,
        }
    )


def _variant(
    decision: SettledDecision,
    context: plan.PlanContext,
    *,
    audit_id: str,
    is_fallback: bool = False,
) -> ProposalVariant:
    """
    Build one answer in full.

    Uses the same translation the pipeline uses for a decision it is acting
    on, so a saved answer and a live one cannot drift apart. Doing it twice
    in two places is how the rule for what a change writes ends up with two
    versions that disagree.
    """
    fragment, primary_edge = plan.writes_for(decision, context, audit_id=audit_id)
    return ProposalVariant(
        action=decision.action,
        target_node_id=decision.target_node_id,
        target_type=decision.target_type,
        confidence=decision.confidence,
        reason=decision.reason,
        retrieval_source=decision.retrieval_source,
        anchor_type=decision.anchor_type,
        anchor_value=decision.anchor_value,
        nodes=[SavedNode.of(node) for node in fragment.nodes],
        edges=[SavedEdge.of(edge) for edge in fragment.edges],
        bookkeeping=list(fragment.bookkeeping),
        primary_edge_table=primary_edge.table if primary_edge else None,
        primary_edge_id=plan.edge_handle(primary_edge),
        delta_description=decision.delta_description,
        summary=_summary_of(decision, is_fallback=is_fallback),
    )


def _runner_up_variant(
    decision: SettledDecision,
    context: plan.PlanContext,
    *,
    audit_id: str,
) -> ProposalVariant | None:
    """
    Build the second reading, where one was offered.

    Skipped when the second reading names a record the search never
    surfaced. Such a reading could not have been acted on when it was fresh
    and offering it now would be worse, not better.
    """
    action = decision.runner_up_action
    if action is None:
        return None

    target_id = decision.runner_up_target_node_id
    target_type = None
    if target_id:
        target_type = _type_of(target_id, decision, context)
        if target_type is None:
            logger.debug(
                "second reading names a record that was never offered: %s", target_id
            )
            return None

    swapped = decision.model_copy(
        update={
            "action": action,
            "target_node_id": target_id,
            "target_type": target_type,
            "confidence": decision.runner_up_confidence or 0.0,
            "runner_up_action": decision.action,
            "runner_up_confidence": decision.confidence,
            "runner_up_target_node_id": decision.target_node_id,
        }
    )
    return _variant(swapped, context, audit_id=audit_id)


def _type_of(
    node_id: str, decision: SettledDecision, context: plan.PlanContext
) -> str | None:
    """
    What kind of record an identifier names, or nothing if it names none.

    The candidates the search offered are asked first, because they are what
    the reading was actually chosen from. Records read back in full are the
    fallback — the same record, arriving by a different route, and not always
    read back at all.
    """
    for candidate in decision.item.candidates:
        if candidate.node_id == node_id:
            return candidate.node_type
    known = context.history.get(node_id)
    return known.node_type if known else None


def _as_fallback(decision: SettledDecision) -> SettledDecision:
    """
    The same finding, recorded as its own separate thing.

    Its target is dropped along with the action, because standing on its own
    is precisely the answer that involves no existing record.
    """
    return decision.model_copy(
        update={
            "action": _FALLBACK_ACTION,
            "target_node_id": None,
            "target_type": None,
            "delta_description": None,
        }
    )


def _summary_of(decision: SettledDecision, *, is_fallback: bool) -> str:
    """One plain sentence saying what taking this answer would mean."""
    if is_fallback:
        return "record this as its own separate thing"

    target = decision.target_node_id or "an existing record"
    phrases: dict[ReconciliationAction, str] = {
        ReconciliationAction.MERGE: f"treat this as the same thing as {target}",
        ReconciliationAction.REINFORCE: f"count this as more evidence for {target}",
        ReconciliationAction.EVOLVE: f"write a newer version of {target}",
        ReconciliationAction.BRANCH: "record this as its own separate thing",
        ReconciliationAction.CONTRADICT: f"record that this clashes with {target}",
        ReconciliationAction.DIALECTIC: f"record this and {target} as a live tension",
        ReconciliationAction.REGULATE: f"record this as interrupting {target}",
        ReconciliationAction.AMBIGUOUS: "wait for a person",
    }
    return phrases.get(decision.action, f"apply {decision.action.value}")


__all__ = ["freeze"]
