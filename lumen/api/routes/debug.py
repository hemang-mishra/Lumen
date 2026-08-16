"""
Ways to see what a past run did.

These read the record of pipeline runs rather than the graph itself. The two
questions here are the ones every complaint about the graph starts with:
what happened during that run, and where did this particular record come
from.

Neither can be answered from the graph alone, and deliberately so — a trace
identifier is not stored on nodes. What each run wrote is logged separately,
which gives both directions of the answer without putting a processing
detail on every record of somebody's history.

There is a third question underneath both of them, which is how anybody
gets a trace identifier in the first place. Listing the recent runs is the
answer, and without it the other two are only useful to somebody who
already knew what to ask.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from lumen.api.deps import get_config, get_ops
from lumen.api.errors import NotFound
from lumen.api.schemas import (
    EpisodeSourceView,
    ProvenanceView,
    RunListView,
    RunSummaryView,
    WrittenMessageView,
)
from lumen.config import AppConfig
from lumen.operational.enums import WriteTarget
from lumen.operational.repositories import OperationalStore
from lumen.operational.schemas import PipelineTrace

router = APIRouter(prefix="/debug", tags=["debug"])

# The most runs one request will list.
MAX_RUNS = 200


@router.get("/traces", response_model=RunListView)
def list_traces(
    limit: int = Query(50, ge=1, le=MAX_RUNS, description="How many to return"),
    ops: OperationalStore = Depends(get_ops),
    config: AppConfig = Depends(get_config),
) -> RunListView:
    """
    Recent runs, newest first.

    Exists because everything else here is keyed by a trace id, and until
    now nothing in the system handed one out. A person looking at a graph
    they do not recognise had no way to find the run that built it.
    """
    return RunListView(
        runs=[
            RunSummaryView.of(record)
            for record in ops.jobs.list_recent(config.user_id, limit=limit)
        ]
    )


@router.get("/traces/{trace_id}", response_model=PipelineTrace)
def get_trace(
    trace_id: str, ops: OperationalStore = Depends(get_ops)
) -> PipelineTrace:
    """
    Everything that happened during one run.

    The job, every stage attempt in order with its timings and the model it
    used, and every record and link the run produced. What went into each
    stage and what came out is kept too, which is what makes a stage
    explainable after the fact rather than only countable.
    """
    trace = ops.jobs.get_trace(trace_id)
    if trace is None:
        raise NotFound("trace", trace_id)
    return trace


@router.get("/nodes/{node_id}/provenance", response_model=ProvenanceView)
def get_provenance(
    node_id: str, ops: OperationalStore = Depends(get_ops)
) -> ProvenanceView:
    """
    Where one record came from.

    Node to run to conversation. This is the reverse lookup the run log
    exists for: without it, a node in the graph is a claim with no way back
    to the writing that produced it.
    """
    job = ops.jobs.find_job_for_node(node_id)
    if job is None:
        raise NotFound("provenance for node", node_id)

    write = _write_for(ops, job.trace_id, node_id)
    return ProvenanceView(
        node_id=node_id,
        job_id=job.job_id,
        trace_id=job.trace_id,
        session_id=job.session_id,
        episode_id=write.episode_id if write else "",
        written_at=write.written_at.isoformat() if write and write.written_at else None,
    )


@router.get("/episodes/{episode_id}/source", response_model=EpisodeSourceView)
def get_episode_source(
    episode_id: str, ops: OperationalStore = Depends(get_ops)
) -> EpisodeSourceView:
    """
    The writing one episode was read from.

    An episode keeps a summary and a hash of its text, never the text — right
    for a store of conclusions, and useless to somebody checking one. What
    was actually written is still in the conversation the run processed, so
    this walks node to run to conversation and reads it from there.
    """
    job = ops.jobs.find_job_for_node(episode_id)
    if job is None:
        raise NotFound("the run behind episode", episode_id)

    buffer = ops.buffers.get_buffer(job.session_id)
    if buffer is None:
        raise NotFound("the conversation behind episode", episode_id)

    return EpisodeSourceView(
        episode_id=episode_id,
        session_id=job.session_id,
        trace_id=job.trace_id,
        event_date=buffer.event_date.isoformat() if buffer.event_date else None,
        session_label=buffer.session_label,
        messages=[
            WrittenMessageView.of(message)
            for message in ops.buffers.get_messages(job.session_id)
        ],
    )


def _write_for(ops: OperationalStore, trace_id: str, node_id: str):
    """
    The log entry recording this record being written to the graph.

    Only the graph write is wanted. A record that was also indexed for
    searching has a second entry, and it says nothing extra about where the
    record came from.

    A run that cannot be found is treated as a run with no writes rather
    than as a separate failure. It cannot happen — the job was just located
    through its own write log — and a branch that can only be reached by a
    bug is a branch nobody will ever have tested.
    """
    trace = ops.jobs.get_trace(trace_id)
    writes = trace.writes if trace else []
    return next(
        (
            write
            for write in writes
            if write.node_id == node_id and write.target is WriteTarget.GRAPH_NODE
        ),
        None,
    )


__all__ = ["router"]
