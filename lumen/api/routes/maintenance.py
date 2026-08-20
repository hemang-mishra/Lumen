"""
The jobs that work over a whole history rather than a moment of it.

Three things live here and they have almost nothing in common except that
none of them belongs to a conversation.

Erasing somebody's data is the one operation in Lumen that cannot be undone,
so it is the one with a preview: a route that counts what would go and
changes nothing, and a route that does it and will only run if the request
says the word this deployment asks for.

The scan for long-running patterns reads every year there is. It is offered
on its own rather than only inside a monthly report so that somebody can ask
the question directly.

And the last one explains a ranking. Retrieval multiplies four numbers
together to decide what a person is shown; once multiplied, the reason a
record placed where it did is gone. This hands the four back.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query

from lumen.api.deps import get_config, get_eraser, get_graph
from lumen.api.errors import BadRequest, Conflict, NotFound
from lumen.api.schemas import (
    ErasureAuditListView,
    ErasureAuditView,
    ErasurePreviewView,
    ErasureReportView,
    ErasureRequestBody,
    ProofChainListView,
    ProofChainView,
    ProofInstanceView,
    RecordScoreView,
)
from lumen.config import AppConfig
from lumen.erasure import ErasureService
from lumen.erasure.contracts import ErasurePlan, ErasureRefused, ErasureReport, ErasureRequest
from lumen.graph import scoring
from lumen.graph.provider import ReadOnlyGraph
from lumen.graph.queries import node_type_of, tidy_row
from lumen.operational.enums import ErasureScope
from lumen.pipeline.macroextraction import proof
from lumen.pipeline.macroextraction.contracts import ProofChain

router = APIRouter(prefix="/maintenance", tags=["maintenance"])


@router.get("/erasure/preview", response_model=ErasurePreviewView)
def preview_erasure(
    eraser: ErasureService = Depends(get_eraser),
    config: AppConfig = Depends(get_config),
    scope: str = Query(default="ENTRY"),
    entry_id: str | None = Query(default=None),
) -> ErasurePreviewView:
    """
    What erasing this would cover. Nothing is changed by asking.

    Deliberately does not require the confirmation phrase. Somebody deciding
    whether to go ahead should not have to type the word that means yes in
    order to find out what yes would mean.
    """
    request = _as_request(
        config, scope=scope, entry_id=entry_id, confirmation=_phrase(config)
    )
    return _as_preview(eraser.preview(request))


@router.post("/erasure", response_model=ErasureReportView)
def run_erasure(
    body: ErasureRequestBody,
    eraser: ErasureService = Depends(get_eraser),
    config: AppConfig = Depends(get_config),
) -> ErasureReportView:
    """
    Erase what was asked for. There is no way back from this.

    Two different refusals come back differently on purpose. A wrong
    confirmation phrase or an entry nobody wrote is something the caller can
    fix and send again; an erasure already running is not wrong at all, it is
    simply not this request's turn.
    """
    request = _as_request(
        config,
        scope=body.scope,
        entry_id=body.entry_id,
        confirmation=body.confirmation,
    )
    try:
        return _as_report(eraser.erase(request, at=_now()))
    except ErasureRefused as refusal:
        raise _refusal_to_answer(refusal) from refusal


@router.get("/erasure/audits", response_model=ErasureAuditListView)
def list_erasure_audits(
    eraser: ErasureService = Depends(get_eraser),
    config: AppConfig = Depends(get_config),
) -> ErasureAuditListView:
    """Every erasure recorded, newest first, with nothing in it about what was erased."""
    audits = [
        ErasureAuditView(
            id=record.id,
            user_id_hash=record.user_id_hash,
            erased_at=record.erased_at,
            nodes_anonymized=record.nodes_anonymized,
            embeddings_deleted=record.embeddings_deleted,
            entry_ids_affected=list(record.entry_ids_affected),
            initiated_by=record.initiated_by.value,
            status=record.status.value,
        )
        for record in eraser.audits(config.user_id)
    ]
    return ErasureAuditListView(audits=audits, count=len(audits))


@router.post("/proof-chains", response_model=ProofChainListView)
def scan_for_proof_chains(
    store: ReadOnlyGraph = Depends(get_graph),
    config: AppConfig = Depends(get_config),
) -> ProofChainListView:
    """
    Look over the whole history for things that keep coming back.

    A read, despite being a POST. It walks every year of somebody's history
    and costs real time, and putting that behind a GET invites a browser or a
    crawler to run it by accident.
    """
    chains = proof.find_proof_chains(store, config=config.maintenance)
    return ProofChainListView(
        chains=[_as_chain(chain) for chain in chains], count=len(chains)
    )


@router.get("/score/{node_id}", response_model=RecordScoreView)
def explain_score(
    node_id: str,
    store: ReadOnlyGraph = Depends(get_graph),
    config: AppConfig = Depends(get_config),
) -> RecordScoreView:
    """
    What this record is worth right now, and why, factor by factor.

    Measured against this instant rather than against a moment a caller
    supplies, because the question being asked is "why did the conversation
    just rank it there".
    """
    row = store.get_node(node_id)
    if row is None:
        raise NotFound("record", node_id)

    tidied = tidy_row(row)
    weights = scoring.weigh(row, now=_now(), config=config.scoring)
    return RecordScoreView(
        node_id=node_id,
        node_type=node_type_of(row),
        signal_weight=weights.signal,
        recency_weight=weights.recency,
        trust_weight=weights.trust,
        frequency_weight=weights.frequency,
        multiplier=weights.multiplier,
        age_band=weights.band.value,
        quiet_days=weights.quiet_days,
        last_seen=weights.last_seen,
        query_frequency=int(tidied.get("query_frequency") or 0),
    )


# ---------------------------------------------------------------------------
# Turning what was asked into what the service takes, and back
# ---------------------------------------------------------------------------


def _as_request(
    config: AppConfig, *, scope: str, entry_id: str | None, confirmation: str
) -> ErasureRequest:
    """
    Read an erasure request, refusing anything the shape rules reject.

    The scope rules live on the model, so an entry-sized erasure with no
    entry is caught in one place rather than in every route that builds one.
    """
    try:
        return ErasureRequest(
            user_id=config.user_id,
            scope=ErasureScope(scope.upper()),
            entry_id=entry_id,
            confirmation=confirmation,
        )
    except ValueError as wrong:
        raise BadRequest(str(wrong)) from wrong


def _refusal_to_answer(refusal: ErasureRefused) -> Exception:
    """
    Which kind of "no" this was.

    An erasure already running is the world being busy rather than the
    request being wrong, and a caller that waits and repeats it will succeed.
    Everything else here is something they have to change first.
    """
    if "already running" in str(refusal):
        return Conflict(str(refusal))
    return BadRequest(str(refusal))


def _as_preview(plan: ErasurePlan) -> ErasurePreviewView:
    """The plan in the shape the web layer hands back."""
    return ErasurePreviewView(
        scope=plan.scope.value,
        entry_id=plan.entry_id,
        records_by_kind=dict(plan.records_by_kind),
        total_records=plan.total_records,
        vectors=plan.vectors,
        conversations=plan.conversations,
        not_reached=list(plan.not_reached),
    )


def _as_report(report: ErasureReport) -> ErasureReportView:
    """The report in the shape the web layer hands back."""
    return ErasureReportView(
        audit_id=report.audit_id,
        scope=report.scope.value,
        entry_id=report.entry_id,
        status=report.status.value,
        records_anonymized=report.records_anonymized,
        vectors_deleted=report.vectors_deleted,
        operational_rows_cleared=report.operational_rows_cleared,
        entry_ids_affected=list(report.entry_ids_affected),
        failures=list(report.failures),
    )


def _as_chain(chain: ProofChain) -> ProofChainView:
    """One proof chain in the shape the web layer hands back."""
    return ProofChainView(
        record_id=chain.record_id,
        record_type=chain.record_type,
        label=chain.label,
        total_instances=chain.total_instances,
        span_days=chain.span_days,
        span_years=chain.span_years,
        summary=chain.summary,
        key_instances=[
            ProofInstanceView(
                episode_id=instance.episode_id,
                date=instance.happened_at.date().isoformat(),
                excerpt=instance.excerpt,
            )
            for instance in chain.key_instances
        ],
    )


def _phrase(config: AppConfig) -> str:
    """The confirmation this deployment asks for, so a preview can pass it."""
    return config.maintenance.erasure_confirm_phrase


def _now() -> datetime:
    """The moment this request is being answered at."""
    return datetime.now(timezone.utc)


__all__ = ["router"]
