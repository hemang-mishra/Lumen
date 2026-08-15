"""
Building the half of the write plan that reconciliation never sees.

Reconciliation decides what today means for the past, and hands back
everything those decisions imply. It knows nothing about the piece of
writing itself, because it was only ever shown what was pulled out of it.

Somebody still has to create the episode record, hang everything extracted
off it, and chain the day's episodes together in the order they were
written. That is this module. It makes no judgements at all — every choice
here follows from the shape of what came out of the earlier stages.

The two halves are merged into one plan on purpose. The plan checks itself
when it is built: no record created twice, no link pointing at something
nobody will create, nothing referring to a record that comes later in the
list. Merging first means those checks cover the whole episode rather than
only the reconciliation part, and a mistake surfaces while planning instead
of halfway through saving.
"""

from __future__ import annotations

import logging
from datetime import datetime

from lumen.schemas.edges import LogicalEdgeType, LumenEdge, resolve_edge_table
from lumen.schemas.enums import ObservationStatus, ReconciliationStatus
from lumen.schemas.nodes import EpisodeNode
from lumen.schemas.pipeline import (
    ExtractionResult,
    GraphWritePlan,
    MicroextractionInput,
    PlannedEdge,
    PlannedNode,
    PreprocessingResult,
    ReconciliationOutcome,
)

logger = logging.getLogger(__name__)

# The name given to the coreference map of an entry. One map covers a whole
# entry, so the entry's own identifier is enough to name it, and building it
# by rule rather than at random means the same entry always points at the
# same map however many times it is processed.
COREFERENCE_ID_PREFIX = "coref_"


def coreference_map_id(entry_id: str) -> str:
    """The identifier of the coreference map belonging to one entry."""
    return f"{COREFERENCE_ID_PREFIX}{entry_id}"


def build_episode_node(
    payload: MicroextractionInput,
    *,
    preprocessing: PreprocessingResult,
    reconciliation_status: ReconciliationStatus,
    at: datetime,
) -> EpisodeNode:
    """
    Create the record that holds one episode together.

    Nothing before this point creates it, even though every stage refers to
    it by name and everything extracted belongs to it. The runner is the
    only place that holds all the pieces at once: the cleaned episode, the
    entry it came from, and how its reconciliation turned out.

    The languages are recorded because what is stored may be a translation.
    Without them, a person's own words in their own language would silently
    become English with nothing saying so.
    """
    episode = payload.episode
    return EpisodeNode(
        node_id=episode.episode_id,
        entry_id=payload.entry_id,
        created_at=at,
        valid_from=at,
        occurred_at=payload.occurred_at,
        event_date=payload.event_date,
        session_label=payload.session_label or episode.episode_id,
        source_modality=payload.source_modality,
        entry_class=episode.entry_class,
        episode_summary=episode.episode_summary,
        historical_era=episode.historical_era,
        overarching_themes=list(episode.overarching_themes),
        episode_index=episode.episode_index,
        total_episodes_in_entry=episode.total_episodes_in_entry,
        coreference_map_id=coreference_map_id(payload.entry_id),
        reconciliation_status=reconciliation_status,
        raw_text_hash=episode.raw_text_hash,
        language_tags=list(preprocessing.detected_languages) or ["en"],
    )


def compose(
    payload: MicroextractionInput,
    extraction: ExtractionResult,
    outcome: ReconciliationOutcome | None,
    *,
    preprocessing: PreprocessingResult,
    reconciliation_status: ReconciliationStatus,
    previous_episode_id: str | None,
    at: datetime,
) -> GraphWritePlan:
    """
    Turn everything one episode produced into a single plan to be saved.

    The order of the records matters and is not arbitrary. The episode comes
    first, then the things that can explain a change — a reflection or an
    event — then the findings, then the cause-and-effect chains and their
    steps. Reconciliation's own records follow, because they can point back
    at any of these.

    No reconciliation outcome means the episode never reached that stage:
    either it was too thin to be worth comparing against the past, or it
    could not be read at all. Both are saved as what they are.
    """
    episode_node = build_episode_node(
        payload,
        preprocessing=preprocessing,
        reconciliation_status=reconciliation_status,
        at=at,
    )

    nodes = [
        PlannedNode(node_type="EpisodeNode", node=episode_node),
        *_extracted_nodes(extraction),
    ]
    edges = [
        *_containment_edges(episode_node.node_id, extraction, at=at),
        *_chain_step_edges(extraction, at=at),
        *_failed_extraction_edges(episode_node.node_id, extraction, at=at),
        *_ordering_edge(episode_node.node_id, previous_episode_id, at=at),
    ]

    known_ids = {previous_episode_id} if previous_episode_id else set()

    if outcome is not None:
        nodes.extend(outcome.write_plan.nodes)
        edges.extend(outcome.write_plan.edges)
        known_ids |= set(outcome.write_plan.existing_node_ids)

    return GraphWritePlan(
        nodes=nodes,
        edges=edges,
        bookkeeping=list(outcome.write_plan.bookkeeping) if outcome else [],
        existing_node_ids=frozenset(known_ids),
    )


# ---------------------------------------------------------------------------
# The records
# ---------------------------------------------------------------------------


def _extracted_nodes(extraction: ExtractionResult) -> list[PlannedNode]:
    """
    Everything pulled out of the episode, in the order it must be created.

    Reflections and events come before findings because a finding may be
    explained by one of them. Chains come before their own steps for the
    same reason.

    Failed findings are created too. They keep whatever the person actually
    wrote and are marked as unreadable rather than thrown away, so the
    writing survives even when the reading of it did not.
    """
    ordered: list[PlannedNode] = []
    ordered += [PlannedNode(node_type="SessionNode", node=n) for n in extraction.sessions]
    ordered += [PlannedNode(node_type="EventNode", node=n) for n in extraction.events]
    ordered += [
        PlannedNode(node_type="ObservationNode", node=n) for n in extraction.observations
    ]
    ordered += [
        PlannedNode(node_type="ObservationNode", node=n)
        for n in extraction.failed_observations
    ]
    ordered += [
        PlannedNode(node_type="CausalChainNode", node=n) for n in extraction.causal_chains
    ]
    ordered += [
        PlannedNode(node_type="CausalStepNode", node=n) for n in extraction.causal_steps
    ]
    return ordered


# ---------------------------------------------------------------------------
# The links
# ---------------------------------------------------------------------------


def _structural_edge(
    logical: LogicalEdgeType,
    from_id: str,
    from_type: str,
    to_id: str,
    to_type: str,
    *,
    at: datetime,
) -> PlannedEdge:
    """
    Build one link that simply records how things are arranged.

    These carry no decision and no confidence because nothing decided them —
    an episode contains what was found in it, and that is a fact about the
    writing rather than a judgement about the person. They are written once
    and never withdrawn.
    """
    return PlannedEdge(
        logical_type=logical,
        table=resolve_edge_table(logical, from_type, to_type),
        from_node_id=from_id,
        to_node_id=to_id,
        edge=LumenEdge(source_node_id=from_id, target_node_id=to_id, valid_from=at),
    )


def _containment_edges(
    episode_id: str, extraction: ExtractionResult, *, at: datetime
) -> list[PlannedEdge]:
    """Link the episode to everything found in it."""
    children: list[tuple[str, str]] = [
        *((node.node_id, "SessionNode") for node in extraction.sessions),
        *((node.node_id, "EventNode") for node in extraction.events),
        *((node.node_id, "ObservationNode") for node in extraction.observations),
        *((node.node_id, "CausalChainNode") for node in extraction.causal_chains),
    ]
    return [
        _structural_edge(
            LogicalEdgeType.CONTAINS, episode_id, "EpisodeNode", child_id, child_type, at=at
        )
        for child_id, child_type in children
    ]


def _chain_step_edges(
    extraction: ExtractionResult, *, at: datetime
) -> list[PlannedEdge]:
    """
    Link each cause-and-effect chain to its own steps.

    A step names the chain it belongs to, so the link is read off the step
    rather than guessed. A step naming a chain that this episode did not
    produce is dropped with a warning: keeping it would point the plan at a
    record nobody is going to create, and the whole episode would be refused
    over one stray step.
    """
    chain_ids = {chain.node_id for chain in extraction.causal_chains}
    edges: list[PlannedEdge] = []

    for step in extraction.causal_steps:
        if step.chain_id not in chain_ids:
            logger.warning(
                "dropping a causal step whose chain is not in this episode",
                extra={"step_id": step.node_id, "chain_id": step.chain_id},
            )
            continue
        edges.append(
            _structural_edge(
                LogicalEdgeType.CHAIN_CONTAINS,
                step.chain_id,
                "CausalChainNode",
                step.node_id,
                "CausalStepNode",
                at=at,
            )
        )
    return edges


def _failed_extraction_edges(
    episode_id: str, extraction: ExtractionResult, *, at: datetime
) -> list[PlannedEdge]:
    """Mark which findings in this episode could not be read properly."""
    return [
        _structural_edge(
            LogicalEdgeType.FAILED_EXTRACTION,
            episode_id,
            "EpisodeNode",
            failed.node_id,
            "ObservationNode",
            at=at,
        )
        for failed in extraction.failed_observations
    ]


def _ordering_edge(
    episode_id: str, previous_episode_id: str | None, *, at: datetime
) -> list[PlannedEdge]:
    """
    Chain this episode to the one written before it in the same entry.

    Only to an episode that actually saved. Episodes are saved one at a
    time, so pointing at one whose save was undone would leave a link to a
    record that does not exist — and the plan would rightly refuse the whole
    episode over it. The runner therefore passes the last episode that
    committed, not the last one it tried.
    """
    if not previous_episode_id:
        return []
    return [
        _structural_edge(
            LogicalEdgeType.FOLLOWS_FROM,
            episode_id,
            "EpisodeNode",
            previous_episode_id,
            "EpisodeNode",
            at=at,
        )
    ]


# ---------------------------------------------------------------------------
# Reading the outcome
# ---------------------------------------------------------------------------


def status_for(
    extraction: ExtractionResult, outcome: ReconciliationOutcome | None
) -> ReconciliationStatus:
    """
    Decide whether an episode is settled or still has something open.

    Three separate things mean "not settled", and all three would otherwise
    look like an ordinary quiet episode: the writing could not be read, no
    decision could be read back, or a decision was made but held for the
    person. An episode that never reached reconciliation because it was too
    thin is genuinely finished, and is marked so.
    """
    if extraction.read_failed:
        return ReconciliationStatus.SUSPENDED
    if outcome is None:
        return ReconciliationStatus.COMPLETE
    if outcome.decision_failed or outcome.escalations:
        return ReconciliationStatus.SUSPENDED
    return outcome.episode_status


def is_thin(extraction: ExtractionResult) -> bool:
    """
    True when nothing in this episode is worth comparing against the past.

    A thin entry's findings are stored as written and never reconciled, so
    searching and deciding would both be paid for and thrown away.
    """
    return not any(
        observation.status is not ObservationStatus.RAW_CAPTURE
        for observation in extraction.observations
    ) and not extraction.events and not extraction.sessions


__all__ = [
    "compose",
    "build_episode_node",
    "coreference_map_id",
    "status_for",
    "is_thin",
]
