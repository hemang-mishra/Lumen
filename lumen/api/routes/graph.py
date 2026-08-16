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
    EpisodeDetailView,
    GraphSliceView,
    GraphStatsView,
    NodeListView,
    NodeView,
    VersionChainView,
)
from lumen.graph.provider import ReadOnlyGraph

router = APIRouter(prefix="/graph", tags=["graph"])

# How far a single request may walk. Beyond three steps a well-connected
# graph is mostly reachable from anywhere in it, so a deeper walk is not a
# more detailed answer — it is the whole history, fetched by accident.
MAX_DEPTH = 3

# The most records one request may return. A cap rather than a suggestion,
# because the caller drawing the result is usually a browser.
MAX_LIMIT = 200


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
    )


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
