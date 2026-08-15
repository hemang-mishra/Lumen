"""
Turning a model's reading into a decision the system will stand behind.

The model proposes; this file disposes. Every rule here is applied in code
after the answer comes back, because each of them is a rule about
consequences rather than about meaning — and consequences are exactly what a
model asked to be helpful will talk itself past.

Six checks run in order, and the order is not arbitrary. An impossible
action is caught before anything is asked about its confidence. A tie is
caught before a threshold, because a tie is not a confidence problem — two
readings at 0.92 and 0.90 are both confident and still a coin toss. The
long-held-belief rule runs before the threshold so that it can raise the bar
the threshold then applies.

Two of the checks change a decision rather than stopping it, and both change
it in the same direction: toward recording today separately and leaving the
past intact. Nothing here ever makes a decision heavier than the model
proposed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from lumen.graph.provider import GraphProvider
from lumen.pipeline.reconciliation.catalog import (
    TIE_WINDOW,
    TRAIT_AGE_DAYS,
    TRIAL_PENALTY_THRESHOLD,
    PromotionTarget,
    is_action_possible,
    promotion_for,
    role_for,
    threshold_for,
)
from lumen.pipeline.reconciliation.contracts import (
    ConfirmedDecision,
    DecisionItem,
    GateRule,
    HistoricalNode,
    ItemDecision,
    ProposedAction,
    SettledDecision,
)
from lumen.schemas.enums import (
    CandidateRetrievalSource,
    ObservationType,
    Provenance,
    ReconciliationAction,
    SignalStrength,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GateContext:
    """
    Everything the checks need to know beyond the decision itself.

    Attributes:
        history: The existing records involved, read back in full and keyed
            by identifier. Needed for age, which no candidate carries.
        graph: The graph, read only, for counting what has been decided
            about a record before.
        has_causal_anchor: Whether this entry produced something that could
            have caused a change — an event, or the session in which the
            thinking happened. A change with no possible cause is not
            recorded as a change.
        now: The moment to measure ages against.
    """

    history: dict[str, HistoricalNode] = field(default_factory=dict)
    graph: GraphProvider | None = None
    has_causal_anchor: bool = True
    now: datetime = field(default_factory=lambda: datetime.now(UTC))


# One check. Takes a decision, hands one back — possibly refused, possibly
# changed, possibly untouched.
Gate = Callable[[SettledDecision, GateContext], SettledDecision]


# ---------------------------------------------------------------------------
# Turning a proposal into something checkable
# ---------------------------------------------------------------------------


def interpret(
    item: DecisionItem,
    proposal: ItemDecision,
    *,
    verdict: ConfirmedDecision | None,
    model_used: str,
) -> SettledDecision:
    """
    Read a model's answer into a decision, without judging it yet.

    A second opinion, where one was asked for, replaces the first answer
    entirely — including its confidence, since a careful model that lowers
    its certainty is telling us something the original number cannot.

    An action nobody recognises is refused here rather than guessed at. The
    alternative is picking the nearest real action, which turns a model's
    typo into a permanent change to someone's history.
    """
    escalated = verdict is not None
    answer = verdict if verdict is not None and not verdict.confirmed else proposal
    payload = verdict if verdict is not None else proposal

    action = _read_action(answer.primary.action)
    runner_up_action = _read_action(
        answer.runner_up.action if answer.runner_up else ""
    )
    target = _target_of(item, answer.primary)

    settled = SettledDecision(
        item=item,
        action=action or ReconciliationAction.BRANCH,
        target_node_id=target.node_id if target else None,
        target_type=target.node_type if target else None,
        confidence=_clamp(answer.primary.confidence),
        runner_up_action=runner_up_action,
        runner_up_confidence=(
            _clamp(answer.runner_up.confidence) if answer.runner_up else None
        ),
        reason=answer.primary.reason,
        escalated=escalated,
        model_used=model_used,
        model_role=role_for(action or ReconciliationAction.BRANCH),
        retrieval_source=(
            target.retrieval_source if target else CandidateRetrievalSource.SEMANTIC
        ),
        anchor_type=target.anchor_type if target else None,
        anchor_value=target.anchor_value if target else None,
        delta_description=payload.delta_description,
        contradiction_summary=payload.contradiction_summary,
        tension_summary=payload.tension_summary,
        regulation_summary=payload.regulation_summary,
        new_node=payload.new_node,
        is_local_extremum=payload.is_local_extremum,
        co_created_origin=item.provenance is Provenance.CO_CREATED,
    )

    if action is None:
        logger.debug("unrecognised action %r", answer.primary.action)
        return settled.refuse(GateRule.UNKNOWN_ACTION)
    return settled


def unreadable(item: DecisionItem, *, model_used: str) -> SettledDecision:
    """
    The decision for a finding no answer came back for.

    Deliberately not a BRANCH. Nothing was decided, and quietly recording
    the finding as new would turn a failed call into a permanent claim that
    the person had never thought this before.
    """
    return SettledDecision(
        item=item,
        action=ReconciliationAction.BRANCH,
        confidence=0.0,
        reason="no usable answer came back for this finding",
        model_used=model_used,
    ).refuse(GateRule.UNREADABLE)


def unsearchable(item: DecisionItem, *, model_used: str) -> SettledDecision:
    """
    The decision for a finding whose past could not be searched.

    This is the quiet failure the whole stage is built around. A search that
    found nothing and a search that never ran look identical, and treating
    the second as the first records a decade-old pattern as a brand-new
    thought — permanently, and with nothing in the graph to show it
    happened.
    """
    return SettledDecision(
        item=item,
        action=ReconciliationAction.BRANCH,
        confidence=0.0,
        reason="the past could not be searched for this finding",
        model_used=model_used,
    ).refuse(GateRule.SEARCH_FAILED)


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------


def check_possible(decision: SettledDecision, context: GateContext) -> SettledDecision:
    """
    Refuse an action that could not be carried out.

    Not every action makes sense between every pair of records — an event
    cannot be "the same as" a belief, and there is no link that would hold
    it. When the second-best reading is possible, it is used instead; asking
    the model again would only produce the same impossible answer at the
    cost of another call.
    """
    if _is_possible(decision, decision.action, decision.target_node_id):
        return decision

    fallback = decision.runner_up_action
    if fallback is not None and _is_possible(
        decision, fallback, decision.target_node_id
    ):
        logger.debug(
            "action %s is not possible here; using the second reading %s",
            decision.action,
            fallback,
        )
        return decision.model_copy(
            update={
                "action": fallback,
                "confidence": decision.runner_up_confidence or decision.confidence,
                "runner_up_action": decision.action,
                "runner_up_confidence": decision.confidence,
                "model_role": role_for(fallback),
                "altered_by": GateRule.IMPOSSIBLE_ACTION,
            }
        )
    return decision.refuse(GateRule.IMPOSSIBLE_ACTION)


def check_tie(decision: SettledDecision, context: GateContext) -> SettledDecision:
    """
    Refuse two readings that are too close to separate.

    This is a tie however confident the model sounds. Being 0.92 sure of one
    thing and 0.90 sure of a different one is not a preference for the
    first; it is a model that cannot tell them apart, saying so in the only
    way it can.
    """
    if decision.runner_up_action is None or decision.runner_up_confidence is None:
        return decision
    if decision.runner_up_action == decision.action:
        return decision
    if abs(decision.confidence - decision.runner_up_confidence) >= TIE_WINDOW:
        return decision
    return decision.model_copy(
        update={"action": ReconciliationAction.AMBIGUOUS}
    ).refuse(GateRule.TIE)


def check_trial_not_trait(
    decision: SettledDecision, context: GateContext
) -> SettledDecision:
    """
    Stop one good day from rewriting a long-held belief.

    Someone who has avoided something for years and manages it once has had
    a moment, not a change of self. The moment is worth recording on its
    own; the belief is left alone until it happens again. Counting past
    decisions about that belief is what makes "again" mean something.

    The one way through is the person saying it themselves. An explicit
    breakthrough they have named and given weight to outranks the system's
    caution, because at that point they know something about themselves that
    no amount of counting will show.
    """
    if decision.action not in (
        ReconciliationAction.EVOLVE,
        ReconciliationAction.CONTRADICT,
    ):
        return decision

    target = context.history.get(decision.target_node_id or "")
    if target is None or not _is_long_held(target, context.now):
        return decision
    if _is_named_breakthrough(decision.item):
        logger.debug("breakthrough named by the person; long-held rule not applied")
        return decision
    if _has_deviated_before(decision.target_node_id, context):
        return decision
    if decision.confidence >= TRIAL_PENALTY_THRESHOLD:
        return decision

    return _record_separately(decision, GateRule.TRIAL_NOT_TRAIT)


def check_local_extremum(
    decision: SettledDecision, context: GateContext
) -> SettledDecision:
    """
    Stop a bad fortnight from becoming someone's new normal.

    Crunch weeks, exam periods and illnesses produce writing that reads like
    collapse, and it is real — but it describes a spike inside a longer
    stretch, not a changed baseline. Those moments are recorded on their
    own, so the stretch they sit inside keeps its shape.
    """
    if decision.action is not ReconciliationAction.EVOLVE:
        return decision
    if not decision.is_local_extremum:
        return decision
    return _record_separately(decision, GateRule.LOCAL_EXTREMUM)


def check_causal_anchor(
    decision: SettledDecision, context: GateContext
) -> SettledDecision:
    """
    Refuse a change with nothing that could have caused it.

    A belief that shifts has to point at something — something that
    happened, or the session in which the person worked it out. That link is
    what makes a version chain readable years later instead of being a list
    of edits with no story. Every reflective entry produces such an anchor,
    so this only fires on a path that should never have reached a change at
    all.
    """
    if decision.action is not ReconciliationAction.EVOLVE:
        return decision
    if context.has_causal_anchor:
        return decision
    return decision.refuse(GateRule.NO_CAUSAL_ANCHOR)


def check_required_wording(
    decision: SettledDecision, context: GateContext
) -> SettledDecision:
    """
    Refuse an action that arrived without the sentence it depends on.

    Four of the actions are meaningless unarticulated: a change nobody can
    say the shape of, a clash nobody states, a tension with no description,
    an interruption of nothing named. The sentence is not decoration — it is
    what makes the record readable years later by someone who no longer
    remembers the day.

    Writing one here to fill the gap would be the system inventing a claim
    about somebody's inner life, so the item waits for a person instead.
    """
    required = _REQUIRED_WORDING.get(decision.action)
    if required is None:
        return decision
    if (getattr(decision, required, None) or "").strip():
        return decision
    return decision.refuse(GateRule.MISSING_WORDING)


def check_confidence(
    decision: SettledDecision, context: GateContext
) -> SettledDecision:
    """
    Hold back a decision the model was not sure enough about.

    Deliberately not downgraded to "record it as new". A low-confidence
    reading that quietly becomes a new record is how a graph fills with
    duplicates of things the person has said for years — and unlike a wrong
    merge, nothing about it ever looks like an error.
    """
    if decision.confidence >= threshold_for(decision.action):
        return decision
    return decision.refuse(GateRule.BELOW_THRESHOLD)


# Which actions cannot be recorded without a sentence of their own, and
# which sentence each one needs.
_REQUIRED_WORDING: dict[ReconciliationAction, str] = {
    ReconciliationAction.EVOLVE: "delta_description",
    ReconciliationAction.CONTRADICT: "contradiction_summary",
    ReconciliationAction.DIALECTIC: "tension_summary",
    ReconciliationAction.REGULATE: "regulation_summary",
}


# The order these run in is part of the design; see the module docstring.
GATES: tuple[Gate, ...] = (
    check_possible,
    check_tie,
    check_trial_not_trait,
    check_local_extremum,
    check_causal_anchor,
    check_required_wording,
    check_confidence,
)


def apply_gates(
    decision: SettledDecision,
    context: GateContext,
    *,
    gates: tuple[Gate, ...] = GATES,
) -> SettledDecision:
    """
    Run every check in order, stopping at the first refusal.

    Stopping early matters: once a decision is being held for a person,
    later checks would only add reasons for something already waiting, and
    the first reason is the one worth showing them.
    """
    for gate in gates:
        if decision.is_refused:
            return decision
        decision = gate(decision, context)
    return decision


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _record_separately(
    decision: SettledDecision, rule: GateRule
) -> SettledDecision:
    """
    Turn a claimed change into a moment recorded on its own.

    The existing record is deliberately kept on the decision even though
    nothing will be linked to it. That is what lets the next occasion be
    counted as a second one: when the same belief is deviated from again,
    this decision is what proves the first time happened.
    """
    return decision.model_copy(
        update={
            "action": ReconciliationAction.BRANCH,
            "runner_up_action": decision.action,
            "runner_up_confidence": decision.confidence,
            "model_role": role_for(ReconciliationAction.BRANCH),
            "altered_by": rule,
        }
    )


def _is_possible(
    decision: SettledDecision,
    action: ReconciliationAction,
    target_node_id: str | None,
) -> bool:
    """Whether this action could actually be carried out for this finding."""
    if action is ReconciliationAction.AMBIGUOUS:
        return True
    target_type = decision.target_type if target_node_id else None
    return is_action_possible(
        action,
        source_type=decision.item.node_type,
        target_type=target_type,
        can_become_standing=_can_become_standing(decision.item),
    )


def _can_become_standing(item: DecisionItem) -> bool:
    """Whether this finding is the sort that can become a belief or a pattern."""
    return promotion_for(item.observation_type) in (
        PromotionTarget.BELIEF,
        PromotionTarget.PATTERN,
    )


def _is_long_held(target: HistoricalNode, now: datetime) -> bool:
    """Whether a record has been in place long enough to count as settled."""
    if target.valid_from is None:
        return False
    age = now - target.valid_from
    return age.days >= TRAIT_AGE_DAYS


def _is_named_breakthrough(item: DecisionItem) -> bool:
    """
    Whether the person themselves called this a breakthrough, and meant it.

    Both halves are required. The category alone is a model's word for it;
    the weight is what says the person treated it as one.
    """
    return (
        item.observation_type is ObservationType.METACOGNITIVE_BREAKTHROUGH
        and item.signal_strength in (SignalStrength.HIGH, SignalStrength.CRITICAL)
    )


def _has_deviated_before(target_node_id: str | None, context: GateContext) -> bool:
    """
    Whether this record has been argued with before.

    Read from the decisions already recorded against it, since that is the
    only place the history of "this has happened before" is kept. A graph
    that cannot answer is treated as having no history, which keeps the
    cautious path rather than the permissive one.
    """
    if target_node_id is None or context.graph is None:
        return False
    try:
        return (
            context.graph.count_prior_decisions(
                target_node_id,
                actions=[
                    ReconciliationAction.BRANCH.value,
                    ReconciliationAction.EVOLVE.value,
                    ReconciliationAction.CONTRADICT.value,
                ],
            )
            > 0
        )
    except Exception:
        logger.warning(
            "could not count earlier decisions; treating as a first occasion",
            extra={"target_node_id": target_node_id},
        )
        return False


def _target_of(item: DecisionItem, proposal: ProposedAction):
    """
    Find the candidate a reading points at.

    A name that matches nothing offered is treated as no target at all,
    which the possibility check then refuses for any action that needs one.
    Looking it up in the graph instead would let a model reconcile against a
    record the search never surfaced.
    """
    if not proposal.target_node_id:
        return None
    for candidate in item.candidates:
        if candidate.node_id == proposal.target_node_id:
            return _CandidateView(candidate)
    logger.debug("answer named a record that was not offered: %s", proposal.target_node_id)
    return None


class _CandidateView:
    """The few things about a candidate a decision needs to carry forward."""

    def __init__(self, candidate) -> None:
        self.node_id = candidate.node_id
        self.node_type = candidate.node_type
        self.retrieval_source = candidate.retrieval_source
        self.anchor_type = candidate.structural_anchor_type
        self.anchor_value = candidate.structural_anchor_value


def _read_action(raw: str) -> ReconciliationAction | None:
    """Read an action name, or nothing if it is not one of the eight."""
    try:
        return ReconciliationAction(raw.strip().upper())
    except (ValueError, AttributeError):
        return None


def _clamp(value: float) -> float:
    """
    Keep a confidence inside 0 to 1.

    Models overshoot: 1.2 and 110 both turn up where a fraction was asked
    for. Anything that is not a number at all never reaches here — the reply
    fails to parse and the whole reading is asked for again.
    """
    return min(1.0, max(0.0, value))


__all__ = [
    "GateContext",
    "Gate",
    "GATES",
    "interpret",
    "unreadable",
    "unsearchable",
    "apply_gates",
    "check_possible",
    "check_tie",
    "check_trial_not_trait",
    "check_local_extremum",
    "check_causal_anchor",
    "check_required_wording",
    "check_confidence",
]
