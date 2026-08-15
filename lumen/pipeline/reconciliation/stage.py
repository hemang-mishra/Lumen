"""
The stage that decides what today means for what came before.

Everything up to here was reversible. An entry can be cleaned again, read
again, searched again — a mistake costs a rerun. This stage is where
something permanent is settled: that today's thought is the same one the
person had in March, or that a belief they held for a year has changed, or
that this is the first time they have ever said it.

Nothing here writes to a database. The decisions are made, and everything
they come to is built and handed back as a plan for whoever owns writing.
That split is what makes it possible to check every consequence of every
decision without a database anywhere near it — and it keeps every judgement
about a person's history in one place instead of spread across the code that
saves things.

Two failures are treated as first-class, because both look exactly like
success from outside. An entry nobody could decide about looks like an entry
with nothing in it. A search that never ran looks like a person having a
brand-new thought. Both would quietly record a lifelong pattern as a fresh
discovery, so both stop and wait for a person instead.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from datetime import UTC, datetime

from lumen.config import AppConfig
from lumen.graph.provider import GraphProvider
from lumen.pipeline.reconciliation import decide, gates, people, plan
from lumen.pipeline.reconciliation.contracts import (
    ConfirmedDecision,
    DecisionItem,
    DecisionResponse,
    GateRule,
    HistoricalNode,
    ItemDecision,
    SettledDecision,
)
from lumen.providers.protocols import LLMProvider
from lumen.schemas.enums import (
    HitlEntryType,
    ObservationStatus,
    Provenance,
    ReconciliationAction,
    ReconciliationStatus,
    SignalStrength,
)
from lumen.schemas.nodes import EventNode, ObservationNode, SessionNode
from lumen.schemas.pipeline import (
    ExtractionResult,
    GraphWritePlan,
    HitlEscalation,
    MicroextractionInput,
    ReconciliationOutcome,
    ReconciliationResult,
    RetrievalResult,
)

logger = logging.getLogger(__name__)


def reconcile(
    extraction: ExtractionResult,
    retrievals: list[RetrievalResult],
    *,
    graph: GraphProvider,
    lightweight: LLMProvider,
    thinking: LLMProvider,
    episode: MicroextractionInput | None = None,
    config: AppConfig | None = None,
) -> ReconciliationOutcome:
    """
    Decide how everything found in one entry relates to the person's past.

    The graph is a parameter, and it is only ever read from: to fill in what
    the search summaries left out, to check what is already known, and to
    count what has been decided before. Everything this stage concludes
    comes back as a plan; none of it is saved here.
    """
    started = time.perf_counter()
    settings = config or AppConfig()
    at = _moment_of(extraction, episode)

    items = _items_to_decide(extraction, retrievals)
    if not items:
        return _empty_outcome(extraction, lightweight, started)

    history = _hydrate_candidates(items, graph=graph)
    decidable = [item for item in items if not item.search_failed]
    response = decide.propose(
        decidable,
        provider=lightweight,
        attempts=settings.pipeline.max_decision_attempts,
    )
    settled, model_used = _settle_all(
        items,
        decidable,
        response,
        history=history,
        graph=graph,
        thinking=thinking,
        lightweight=lightweight,
        extraction=extraction,
        at=at,
        attempts=settings.pipeline.max_decision_attempts,
    )

    outcome = _assemble(
        settled,
        extraction=extraction,
        response=response,
        history=history,
        graph=graph,
        at=at,
        model_used=model_used,
        decision_failed=response is None,
    )
    outcome = outcome.model_copy(
        update={"decision_time_ms": int((time.perf_counter() - started) * 1000)}
    )
    _log_outcome(outcome, settled)
    return outcome


# ---------------------------------------------------------------------------
# Gathering what has to be decided
# ---------------------------------------------------------------------------


def _moment_of(
    extraction: ExtractionResult, episode: MicroextractionInput | None
) -> datetime:
    """
    The moment everything this stage creates is stamped with.

    The entry's own moment, not the moment the pipeline happened to run. An
    entry processed days late belongs to the day it was written, and its
    identifiers should say so — a graph is much easier to read by eye when
    the dates in it line up with the person's memory of events.
    """
    if episode is not None:
        return episode.occurred_at
    for node in (*extraction.observations, *extraction.sessions, *extraction.events):
        return node.occurred_at
    return datetime.now(UTC)


def _items_to_decide(
    extraction: ExtractionResult, retrievals: list[RetrievalResult]
) -> list[DecisionItem]:
    """
    Pair every searched finding with what the search brought back for it.

    Anything the search skipped is skipped here too, and for the same
    reason: findings from a thin entry are saved as they are and never
    compared against the past, so there is nothing for this stage to decide
    about them.
    """
    found = _extracted_by_id(extraction)
    items: list[DecisionItem] = []

    for result in retrievals:
        source = found.get(result.source_node_id)
        if source is None:
            logger.debug(
                "search result has no matching finding", extra={"node_id": result.source_node_id}
            )
            continue
        items.append(_as_item(source, result))

    return items


def _extracted_by_id(
    extraction: ExtractionResult,
) -> dict[str, ObservationNode | EventNode | SessionNode]:
    """Everything extracted, keyed by identifier, minus what never gets reconciled."""
    return {
        node.node_id: node
        for node in (
            *(
                observation
                for observation in extraction.observations
                if observation.status is not ObservationStatus.RAW_CAPTURE
            ),
            *extraction.events,
            *extraction.sessions,
        )
    }


def _as_item(
    source: ObservationNode | EventNode | SessionNode, result: RetrievalResult
) -> DecisionItem:
    """Reduce one finding and its candidates to what deciding actually needs."""
    if isinstance(source, ObservationNode):
        text, kind = source.content, "ObservationNode"
    elif isinstance(source, EventNode):
        text, kind = source.event_summary, "EventNode"
    else:
        text, kind = source.session_summary, "SessionNode"

    # Only findings record whose idea they were. Something that happened, and
    # the session it was thought about in, are always the person's own.
    return DecisionItem(
        source=source,
        node_type=kind,
        text=text,
        observation_type=getattr(source, "type", None),
        signal_strength=source.signal_strength,
        provenance=getattr(source, "provenance", Provenance.USER_GENERATED),
        episode_id=source.episode_id,
        person_refs=tuple(getattr(source, "person_refs", ())),
        candidates=(*result.pass_a_candidates, *result.pass_b_candidates),
        search_failed=result.search_failed,
    )


# ---------------------------------------------------------------------------
# Deciding
# ---------------------------------------------------------------------------


def _settle_all(
    items: list[DecisionItem],
    decidable: list[DecisionItem],
    response: DecisionResponse | None,
    *,
    history: dict[str, HistoricalNode],
    graph: GraphProvider,
    thinking: LLMProvider,
    lightweight: LLMProvider,
    extraction: ExtractionResult,
    at: datetime,
    attempts: int,
) -> tuple[list[SettledDecision], str]:
    """
    Turn every finding into a decision that has been through the checks.

    The findings whose search failed never reach a model at all. There is
    nothing to decide against, and asking anyway would produce a confident
    answer about a history nobody managed to look at.
    """
    if response is None:
        return (
            [gates.unreadable(item, model_used=lightweight.model_name) for item in items],
            lightweight.model_name,
        )

    proposals = decide.align(response, len(decidable))
    verdicts = _second_opinions(
        decidable, proposals, provider=thinking, attempts=attempts
    )
    context = gates.GateContext(
        history=history,
        graph=graph,
        has_causal_anchor=bool(extraction.sessions or extraction.events),
        now=at,
    )

    model_used = thinking.model_name if verdicts else lightweight.model_name
    positions = {item.node_id: index for index, item in enumerate(decidable, start=1)}
    settled: list[SettledDecision] = []

    for item in items:
        if item.search_failed:
            settled.append(gates.unsearchable(item, model_used=lightweight.model_name))
            continue

        position = positions[item.node_id]
        proposal = proposals.get(position)
        if proposal is None:
            settled.append(gates.unreadable(item, model_used=lightweight.model_name))
            continue

        verdict = verdicts.get(position)
        decision = gates.interpret(
            item,
            proposal,
            verdict=verdict,
            model_used=(
                thinking.model_name if verdict is not None else lightweight.model_name
            ),
        )
        settled.append(gates.apply_gates(decision, context))

    return settled, model_used


def _second_opinions(
    items: list[DecisionItem],
    proposals: dict[int, ItemDecision],
    *,
    provider: LLMProvider,
    attempts: int,
) -> dict[int, ConfirmedDecision]:
    """
    Put the high-consequence readings to the careful model, if there are any.

    An entry with nothing risky in it costs one call. Only the readings that
    permanently alter something long-held pay for a second.
    """
    risky = [
        proposal for proposal in proposals.values() if decide.needs_confirming(proposal)
    ]
    if not risky:
        return {}
    return decide.confirm(items, risky, provider=provider, attempts=attempts)


def _hydrate_candidates(
    items: list[DecisionItem], *, graph: GraphProvider
) -> dict[str, HistoricalNode]:
    """
    Read every candidate record back in full, once.

    A candidate from the search carries enough to recognise a record by and
    no more. Deciding needs its age, and building a new version of it needs
    every field it has. The same record routinely comes back for several
    findings in one entry, so this is done once for all of them.
    """
    wanted = {
        candidate.node_id for item in items for candidate in item.candidates
    }
    if not wanted:
        return {}

    try:
        rows = graph.get_nodes_by_ids(sorted(wanted))
    except Exception:
        logger.warning("could not read the candidate records back")
        return {}

    return {
        str(row["node_id"]): HistoricalNode(
            node_id=str(row["node_id"]),
            node_type=str(row.get("_label", "unknown")),
            preview=str(row.get("content") or row.get("pattern_name") or ""),
            valid_from=_read_moment(row.get("valid_from")),
            era_tag=row.get("era_tag"),
            row=dict(row),
        )
        for row in rows
        if row.get("node_id")
    }


def _read_moment(raw) -> datetime | None:
    """
    Read a stored timestamp, whatever form it comes back in.

    An unreadable date is treated as no date, which makes a record look
    recent — and a record that looks recent is protected by fewer rules, not
    more, so this deliberately never guesses a date that would unlock a
    heavier action.
    """
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=UTC)
    if isinstance(raw, str) and raw:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


# ---------------------------------------------------------------------------
# Turning decisions into everything they come to
# ---------------------------------------------------------------------------


def _assemble(
    settled: list[SettledDecision],
    *,
    extraction: ExtractionResult,
    response: DecisionResponse | None,
    history: dict[str, HistoricalNode],
    graph: GraphProvider,
    at: datetime,
    model_used: str,
    decision_failed: bool,
) -> ReconciliationOutcome:
    """
    Build the decisions, their notes, the plan, and the list of what is
    waiting.

    Findings the person could not be asked about yet still produce a note
    and a queue entry, and produce nothing else. That is the difference
    between an entry that was decided and an entry that is still open.
    """
    context = plan.PlanContext(
        at=at,
        event_date=at.date(),
        history=history,
        exists=_existence_check(graph),
        anchor_node_id=_anchor_id(extraction),
        anchor_node_type=_anchor_type(extraction),
    )

    results: list[ReconciliationResult] = []
    audits = []
    fragment = plan.PlanFragment()
    escalations: list[HitlEscalation] = []

    for sequence, decision in enumerate(settled, start=1):
        piece, audit = plan.plan_for(decision, context, sequence=sequence)
        fragment = fragment.merged_with(piece)
        audits.append(audit)
        results.append(_result_of(decision, audit))
        if decision.is_refused:
            escalations.append(_escalation_of(decision, audit))

    person_nodes, person_edges, person_updates = people.resolve_people(
        [decision.item for decision in settled],
        list(response.people) if response else [],
        graph=graph,
        at=at,
    )

    write_plan = GraphWritePlan(
        nodes=[*person_nodes, *fragment.nodes],
        edges=[*fragment.edges, *person_edges],
        bookkeeping=[*fragment.bookkeeping, *person_updates],
        existing_node_ids=_known_ids(settled, extraction),
    )

    return ReconciliationOutcome(
        episode_id=extraction.episode_id,
        results=results,
        audit_nodes=audits,
        write_plan=write_plan,
        escalations=escalations,
        episode_status=(
            ReconciliationStatus.SUSPENDED
            if escalations
            else ReconciliationStatus.COMPLETE
        ),
        decision_model=model_used,
        decision_failed=decision_failed,
    )


def _result_of(decision: SettledDecision, audit) -> ReconciliationResult:
    """
    State one decision in the form the rest of the pipeline reads.

    A refused decision is reported as what it would have been, marked as
    waiting for a person. Reporting it as nothing at all would lose the only
    record of what the system thought.
    """
    return ReconciliationResult(
        source_node_id=decision.item.node_id,
        action=decision.action,
        target_node_id=decision.target_node_id,
        confidence=decision.confidence,
        delta_description=(
            decision.delta_description
            if decision.action is ReconciliationAction.EVOLVE
            else None
        ),
        decision_model=decision.model_used,
        escalated_to_hitl=decision.is_refused,
        audit_node_id=audit.node_id,
    )


def _escalation_of(decision: SettledDecision, audit) -> HitlEscalation:
    """
    Describe one undecided item well enough to ask about it later.

    Its weight is carried across because that is what decides the order
    people see things in. A critical finding waiting behind twenty ordinary
    ones is a queue nobody finishes.
    """
    return HitlEscalation(
        audit_node_id=audit.node_id,
        source_node_id=decision.item.node_id,
        episode_id=decision.item.episode_id,
        entry_type=(
            HitlEntryType.AMBIGUOUS_TIE
            if decision.refusal is GateRule.TIE
            else HitlEntryType.BELOW_THRESHOLD
        ),
        signal_strength=decision.item.signal_strength or SignalStrength.STANDARD,
        summary=_waiting_summary(decision),
    )


def _waiting_summary(decision: SettledDecision) -> str:
    """One line saying what is undecided and why, with no journal text in it."""
    rule = decision.refusal.value if decision.refusal else "UNKNOWN"
    target = decision.target_node_id or "no existing record"
    return f"{decision.action.value} against {target} held back: {rule}"


def _existence_check(graph: GraphProvider):
    """
    Ask the graph whether an identifier is taken.

    A graph that cannot answer is treated as having nothing, which risks
    planning a record that already exists. That fails loudly while saving,
    which is the right direction: the alternative is inventing a new
    identifier every time and quietly splitting one idea into many.
    """

    def _exists(node_id: str) -> bool:
        try:
            return graph.get_node(node_id) is not None
        except Exception:
            logger.warning("could not check whether a record exists")
            return False

    return _exists


def _anchor_id(extraction: ExtractionResult) -> str | None:
    """
    What a change in this entry can be attributed to.

    The session the thinking happened in is preferred over anything that
    happened, because a shift recorded during reflection was reached by
    reflecting. Where there was no session, the first event stands in.
    """
    if extraction.sessions:
        return extraction.sessions[0].node_id
    if extraction.events:
        return extraction.events[0].node_id
    return None


def _anchor_type(extraction: ExtractionResult) -> str | None:
    """Which kind of thing the anchor is."""
    if extraction.sessions:
        return "SessionNode"
    if extraction.events:
        return "EventNode"
    return None


def _known_ids(
    settled: list[SettledDecision], extraction: ExtractionResult
) -> frozenset[str]:
    """
    Everything a link may point at without this plan creating it.

    Two kinds. Records already in the graph, which the candidates came from.
    And everything this same run extracted, which is saved just before the
    plan runs — an ordering the plan depends on and states out loud rather
    than assuming.
    """
    from_graph = {
        candidate.node_id
        for decision in settled
        for candidate in decision.item.candidates
    }
    from_this_run = {
        node.node_id
        for node in (
            *extraction.observations,
            *extraction.events,
            *extraction.sessions,
        )
    } | {extraction.episode_id}
    return frozenset(from_graph | from_this_run)


def _empty_outcome(
    extraction: ExtractionResult, provider: LLMProvider, started: float
) -> ReconciliationOutcome:
    """The result for an entry with nothing to decide about."""
    return ReconciliationOutcome(
        episode_id=extraction.episode_id,
        decision_model=provider.model_name,
        decision_time_ms=int((time.perf_counter() - started) * 1000),
    )


def _log_outcome(
    outcome: ReconciliationOutcome, settled: list[SettledDecision]
) -> None:
    """
    Record one line saying what was decided.

    The per-action counts are the only warning this stage has. Nothing here
    fails loudly: a model drifting toward joining everything blurs distinct
    ideas into one, and a model drifting toward recording everything
    separately shatters one idea into twenty. Both look like a working
    system from every angle except reading the graph by hand, and both show
    up here first as a number that has moved.
    """
    counts = Counter(decision.action.value for decision in settled)
    refusals = Counter(
        decision.refusal.value for decision in settled if decision.refusal
    )
    logger.info(
        "reconciliation complete",
        extra={
            "episode_id": outcome.episode_id,
            "decided": len(settled),
            "actions": dict(counts),
            "held_back": dict(refusals),
            "records_planned": len(outcome.write_plan.nodes),
            "links_planned": len(outcome.write_plan.edges),
            "waiting_for_a_person": len(outcome.escalations),
            "episode_status": outcome.episode_status.value,
            "decision_failed": outcome.decision_failed,
            "duration_ms": outcome.decision_time_ms,
        },
    )


__all__ = ["reconcile"]
