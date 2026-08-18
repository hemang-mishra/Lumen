"""
Ways to look inside the knowledge graph.

Every route here does the same three things: check what was asked, ask the
store one question, and shape the answer. There is deliberately no logic
worth testing in this file — anything a route decided for itself would be a
judgement about someone's history made in the web layer, which is the last
place it should live.

Nothing here can change anything. The store arrives as a reader, so a write
is not merely absent by choice; the method is not on the object.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query

from lumen.api.deps import get_graph
from lumen.api.errors import NotFound
from lumen.api.schemas import (
    DecisionHistoryView,
    DecisionOutcomeView,
    EpisodeDetailView,
    GraphSliceView,
    GraphStatsView,
    NodeListView,
    NodeView,
    VersionChainView,
)
from lumen.graph.provider import ReadOnlyGraph
from lumen.graph.queries import node_type_of
from lumen.schemas.enums import DecisionStatus

router = APIRouter(prefix="/graph", tags=["graph"])

# How far a single request may walk. Beyond three steps a well-connected
# graph is mostly reachable from anywhere in it, so a deeper walk is not a
# more detailed answer — it is the whole history, fetched by accident.
MAX_DEPTH = 3

# The most records one request may return. A cap rather than a suggestion,
# because the caller drawing the result is usually a browser.
MAX_LIMIT = 200

# Where a decision stands when it was recorded but not acted on. Everything
# in here is waiting for a person: a tie nobody could settle, or a reading
# that was not sure enough to act on by itself. A decision in one of these
# states has changed nothing in the history yet, which is the opposite of
# what a note of a decision looks like at a glance.
HELD_BACK = frozenset(
    {
        DecisionStatus.PENDING_HITL.value,
        DecisionStatus.BELOW_THRESHOLD.value,
        DecisionStatus.SUSPENDED_QUEUE_FULL.value,
    }
)


@router.get("/stats", response_model=GraphStatsView)
def graph_stats(store: ReadOnlyGraph = Depends(get_graph)) -> GraphStatsView:
    """How many records of each kind exist, retired ones included."""
    return GraphStatsView.of(store.count_by_type())


@router.get("/nodes", response_model=NodeListView)
def list_nodes(
    store: ReadOnlyGraph = Depends(get_graph),
    types: list[str] | None = Query(None, description="Kinds of record to include"),
    since: datetime | None = Query(None, description="Only records valid from this date"),
    until: datetime | None = Query(None, description="Only records valid up to this date"),
    domain: str | None = Query(None, description="Part of life, where recorded"),
    signal: str | None = Query(None, description="How much weight a record carries"),
    era: str | None = Query(None, description="Named period of the past"),
    active_only: bool = Query(True, description="Leave out superseded records"),
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
) -> NodeListView:
    """
    List records, newest first.

    A filter a kind of record cannot answer is skipped for that kind rather
    than failing the request. There is no such thing as an observation about
    work — an observation records no part of life — and a caller should not
    need to know which columns each kind keeps in order to ask.
    """
    rows = store.find_nodes(
        types or [],
        since=since,
        until=until,
        domain=domain,
        signal_strength=signal,
        era_tag=era,
        active_only=active_only,
        limit=limit,
        offset=offset,
    )
    return NodeListView.of(rows, limit=limit, offset=offset)


@router.get("/nodes/{node_id}", response_model=NodeView)
def get_node(node_id: str, store: ReadOnlyGraph = Depends(get_graph)) -> NodeView:
    """One record in full."""
    row = store.get_node(node_id)
    if row is None:
        raise NotFound("node", node_id)
    return NodeView.of(row)


@router.get("/nodes/{node_id}/neighbors", response_model=GraphSliceView)
def get_neighbors(
    node_id: str,
    store: ReadOnlyGraph = Depends(get_graph),
    depth: int = Query(1, ge=1, le=MAX_DEPTH),
    edge_types: list[str] | None = Query(None, description="Kinds of link to follow"),
    direction: str = Query("both", pattern="^(in|out|both)$"),
    as_of: datetime | None = Query(None, description="The graph as it stood then"),
    include_invalidated: bool = Query(
        False, description="Follow links a rollback withdrew"
    ),
    limit: int = Query(MAX_LIMIT, ge=1, le=MAX_LIMIT),
) -> GraphSliceView:
    """
    Everything within a few steps of one record, and the links between.

    Withdrawn links are not followed unless asked for: a decision that was
    rolled back should not still shape what the graph appears to say. Asked
    about a past date, a link withdrawn after that date was still live then
    and is followed.
    """
    if store.get_node(node_id) is None:
        raise NotFound("node", node_id)

    return GraphSliceView.of(
        store.get_neighborhood(
            node_id,
            depth=depth,
            edge_types=edge_types,
            direction=direction,
            as_of=as_of,
            include_invalidated=include_invalidated,
            limit=limit,
        )
    )


@router.get("/nodes/{node_id}/versions", response_model=VersionChainView)
def get_versions(
    node_id: str, store: ReadOnlyGraph = Depends(get_graph)
) -> VersionChainView:
    """
    Every version of a belief or pattern, oldest first.

    Reads the same from any point in the chain. Somebody who reached a
    record through a search has no idea whether they are looking at the
    first version or the fifth.

    A kind of record that is never versioned answers with an empty history
    rather than an error — it genuinely has none.
    """
    if store.get_node(node_id) is None:
        raise NotFound("node", node_id)
    return VersionChainView.of(store.get_version_chain(node_id))


@router.get("/nodes/{node_id}/decisions", response_model=DecisionHistoryView)
def get_decisions(
    node_id: str, store: ReadOnlyGraph = Depends(get_graph)
) -> DecisionHistoryView:
    """
    Every decision recorded about one record, newest first.

    This is the answer to "why does the system think this": what was
    compared, what was chosen, how sure it was, and what reversing it
    would take.
    """
    if store.get_node(node_id) is None:
        raise NotFound("node", node_id)
    return DecisionHistoryView(
        node_id=node_id,
        decisions=[NodeView.of(row) for row in store.get_decision_history(node_id)],
    )


@router.get("/episodes/{episode_id}", response_model=EpisodeDetailView)
def get_episode(
    episode_id: str, store: ReadOnlyGraph = Depends(get_graph)
) -> EpisodeDetailView:
    """
    One piece of writing and everything it produced.

    Follows only the links meaning "this came out of that". An episode also
    points at the one written before it, and following that would answer a
    wider question than the one asked.
    """
    contents = store.get_episode_contents(episode_id)
    if not contents.nodes:
        raise NotFound("episode", episode_id)

    return EpisodeDetailView(
        episode=NodeView.of(contents.nodes[0]),
        contents=GraphSliceView.of(contents),
        outcomes=_outcomes_for(contents, store),
    )


def _outcomes_for(contents, store: ReadOnlyGraph) -> list[DecisionOutcomeView]:
    """
    What was decided about each finding this episode produced.

    Gathered here rather than left to the caller to fetch one record at a
    time. The decisions are the point of the episode — a finding that became
    a belief and a finding that was held back for a person look identical
    without them — and a page that has to make ten requests to find that out
    will show it late or not at all.

    The record each decision was made against is looked up too, so the answer
    can say what a finding became rather than showing an identifier and
    leaving the reader to go and resolve it.
    """
    decisions: list[tuple[str, dict]] = []
    became: dict[str, dict] = {}
    for row in contents.nodes[1:]:
        node_id = str(row.get("node_id") or "")
        if not node_id:
            continue
        decisions.extend(
            (node_id, audit) for audit in store.get_decision_history(node_id)
        )
        became.update(_what_it_became(node_id, store))

    targets = _targets_of(decisions, store)
    return [
        _as_outcome(source_id, audit, targets, became)
        for source_id, audit in decisions
    ]


def _what_it_became(node_id: str, store: ReadOnlyGraph) -> dict[str, dict]:
    """
    The lasting records one finding turned into, keyed by the decision that
    made each.

    Matched by the decision's own identifier, which every link a decision
    draws carries. Guessing from the kind of link would be close but not
    exact — one finding can be decided about more than once, and attaching
    the wrong outcome to a decision is the sort of wrong answer that looks
    right.
    """
    slice_ = store.get_neighborhood(node_id, depth=1, direction="out")
    by_id = {str(row.get("node_id")): row for row in slice_.nodes}

    found: dict[str, dict] = {}
    for edge in slice_.edges:
        decision_id = edge.properties.get("decision_id")
        target = by_id.get(edge.to_node_id)
        if decision_id and target is not None and edge.to_node_id != node_id:
            found[str(decision_id)] = target
    return found


def _targets_of(
    decisions: list[tuple[str, dict]], store: ReadOnlyGraph
) -> dict[str, dict]:
    """Fetch every record the decisions point at, in one go."""
    wanted = sorted(
        {
            str(audit.get("target_node_id"))
            for _, audit in decisions
            if audit.get("target_node_id")
        }
    )
    if not wanted:
        return {}
    return {
        str(row.get("node_id")): row for row in store.get_nodes_by_ids(wanted)
    }


def _as_outcome(
    source_id: str, audit: dict, targets: dict[str, dict], became: dict[str, dict]
) -> DecisionOutcomeView:
    """Shape one decision the way somebody reading it needs it."""
    target_id = audit.get("target_node_id") or None
    target = targets.get(str(target_id)) if target_id else None
    status = str(audit.get("status") or "")
    made = became.get(str(audit.get("node_id") or ""))

    return DecisionOutcomeView(
        source_node_id=source_id,
        action=str(audit.get("action") or ""),
        target_node_id=target_id,
        target_type=node_type_of(target) if target else None,
        target_preview=_preview_of(target) if target else None,
        became_node_id=str(made.get("node_id")) if made else None,
        became_type=node_type_of(made) if made else None,
        became_preview=_preview_of(made) if made else None,
        edge_type_created=audit.get("edge_type_created") or None,
        confidence=audit.get("confidence"),
        runner_up_action=audit.get("runner_up_action") or None,
        runner_up_confidence=audit.get("confidence_runner_up"),
        status=status,
        # A decision that was recorded and a decision that was acted on look
        # the same in the note itself. Only the status tells them apart, and
        # it is the difference between "this is in your history now" and
        # "somebody still has to look at this".
        waiting_for_a_person=status in HELD_BACK,
        model_used=audit.get("model_used") or None,
        decided_at=str(audit["created_at"]) if audit.get("created_at") else None,
        decision_id=str(audit.get("node_id") or ""),
    )


def _preview_of(row: dict) -> str:
    """The first thing a record says that a person would recognise it by."""
    for field in (
        "pattern_name",
        "belief_statement",
        "lesson_statement",
        "principle_name",
        "content",
        "event_summary",
        "session_summary",
        "episode_summary",
        "loop_description",
        "canonical_name",
    ):
        value = row.get(field)
        if value:
            return str(value)
    return ""


@router.get("/chains/{chain_id}", response_model=NodeListView)
def get_chain(
    chain_id: str, store: ReadOnlyGraph = Depends(get_graph)
) -> NodeListView:
    """
    One cause-and-effect sequence's steps, in the order they happened.

    Order is the whole content of a sequence: read in a different order it
    describes a different sequence.
    """
    if store.get_node(chain_id) is None:
        raise NotFound("chain", chain_id)

    steps = store.get_causal_chain(chain_id)
    return NodeListView.of(steps, limit=max(len(steps), 1), offset=0)


__all__ = ["router"]
