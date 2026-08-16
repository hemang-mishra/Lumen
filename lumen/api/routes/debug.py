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
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from lumen.api.deps import get_ops
from lumen.api.errors import NotFound
from lumen.api.schemas import ProvenanceView
from lumen.operational.enums import WriteTarget
from lumen.operational.repositories import OperationalStore
from lumen.operational.schemas import PipelineTrace

router = APIRouter(prefix="/debug", tags=["debug"])


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
