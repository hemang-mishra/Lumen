"""
Getting an exported conversation into Lumen, and watching it arrive.

This is the only part of the web layer that causes anything to be written,
and it is worth being precise about how little of that power lives here. A
route reads a file, asks the parser what it means, asks the loader to store
it, and puts an identifier on a queue. It never holds the graph, never holds
the vector store, and never calls the pipeline. Everything that can change
somebody's history lives behind the worker, on its own thread.

Two ways in, one path. A browser sends a file as a form upload; a script
sends the same JSON as a request body. They differ in how the bytes arrive
and in nothing else.

The answer is 202, not 200. One conversation is several model calls and
takes minutes, so what comes back is a receipt with the identifiers already
settled — enough to follow the work, handed over before the work starts.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, File, Query, UploadFile, status

from lumen.auth import Identity
from lumen.api.errors import BadRequest, NotFound, Unavailable
from lumen.api.schemas import (
    BatchStatusView,
    ConversationReceipt,
    ImportView,
    RejectionView,
    UploadReceipt,
)
from lumen.config import AppConfig
from lumen.ingest import ExportFormatError, IngestWorker, parse_export, stage_conversations
from lumen.ingest.contracts import ImportPlan, StagedConversation
from lumen.operational.repositories import OperationalStore
from lumen.api.deps import get_config, get_ops, get_worker, require_identity
from lumen.providers.errors import ProviderError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ingest", tags=["ingest"])

# Uploads larger than this are refused before being decoded. A chat export
# is text; anything approaching this size is a mistake, and finding that out
# after parsing it costs the memory the mistake was going to cost anyway.
MAX_UPLOAD_BYTES = 64 * 1024 * 1024

# The most history rows one request will hand back.
MAX_HISTORY = 200


@router.post(
    "/file",
    response_model=UploadReceipt,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload an exported conversation as a file",
)
async def upload_file(
    file: UploadFile = File(..., description="A chat export, as JSON"),
    identity: Identity = Depends(require_identity),
    ops: OperationalStore = Depends(get_ops),
    worker: IngestWorker = Depends(get_worker),
    config: AppConfig = Depends(get_config),
) -> UploadReceipt:
    """
    Take an exported conversation from a form upload and start processing it.

    What a browser sends. The body arrives as multipart form data, which is
    the only thing this does that the JSON route does not.
    """
    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise BadRequest(
            f"that file is {len(raw) // 1_048_576} MB, and the limit is "
            f"{MAX_UPLOAD_BYTES // 1_048_576} MB"
        )

    return _accept(
        _decode(raw),
        filename=file.filename or "",
        ops=ops,
        worker=worker,
        config=config,
        identity=identity,
    )


@router.post(
    "/json",
    response_model=UploadReceipt,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Send an exported conversation as JSON",
)
def upload_json(
    payload: Any = Body(..., description="A conversation object, or a list of them"),
    filename: str = Query("", description="What to call this in the history"),
    identity: Identity = Depends(require_identity),
    ops: OperationalStore = Depends(get_ops),
    worker: IngestWorker = Depends(get_worker),
    config: AppConfig = Depends(get_config),
) -> UploadReceipt:
    """
    Take an exported conversation from a request body and start processing it.

    The direct route, for anything that is not a browser. Identical in every
    respect to the upload except that the JSON has already been decoded.
    """
    return _accept(
        payload,
        filename=filename,
        ops=ops,
        worker=worker,
        config=config,
        identity=identity,
    )


@router.get(
    "/imports",
    response_model=list[ImportView],
    summary="Everything that has been imported",
)
def list_imports(
    limit: int = Query(50, ge=1, le=MAX_HISTORY, description="How many to return"),
    ops: OperationalStore = Depends(get_ops),
    config: AppConfig = Depends(get_config),
    identity: Identity = Depends(require_identity),
) -> list[ImportView]:
    """Past imports, newest first."""
    return [
        ImportView.of(record)
        for record in ops.imports.list_recent(identity.user_id, limit=limit)
    ]


@router.get(
    "/imports/{batch_id}",
    response_model=BatchStatusView,
    summary="How one upload is getting on",
)
def get_batch(
    batch_id: str, ops: OperationalStore = Depends(get_ops)
) -> BatchStatusView:
    """
    Where every conversation in one upload has got to.

    What the page polls. `finished` is the signal to stop: it is true once
    nothing in the upload will change again on its own.
    """
    batch = ops.imports.get_batch(batch_id)
    if batch is None:
        raise NotFound("upload", batch_id)
    return BatchStatusView(
        batch_id=batch.batch_id,
        filename=batch.filename,
        finished=batch.finished,
        imports=[ImportView.of(record) for record in batch.imports],
    )


# ---------------------------------------------------------------------------
# The one path both routes take
# ---------------------------------------------------------------------------


def _accept(
    payload: Any,
    *,
    filename: str,
    ops: OperationalStore,
    worker: IngestWorker,
    config: AppConfig,
    identity: Identity,
) -> UploadReceipt:
    """
    Read a file, store what it held, and queue the work.

    The order matters. Whether a model can be reached is checked before
    anything is written, so a service with no credential configured refuses
    the upload outright instead of accepting it, storing it, and reporting
    a failure four minutes later that looks like a problem with the file.
    """
    _require_a_working_model(worker)

    plan = _parse(payload, filename, config)
    batch_id = f"batch_{uuid.uuid4().hex[:12]}"
    staged = stage_conversations(
        plan, ops=ops, user_id=identity.user_id, batch_id=batch_id
    )

    queued = [item for item in staged if not item.already_imported]
    for item in queued:
        worker.submit(item.import_id)

    logger.info(
        "accepted an upload",
        extra={
            "batch_id": batch_id,
            "source_file": filename,
            "queued": len(queued),
            "already_imported": len(staged) - len(queued),
            "rejected": len(plan.rejected),
        },
    )

    return UploadReceipt(
        batch_id=batch_id,
        filename=plan.filename,
        queued=len(queued),
        conversations=[_receipt(item) for item in staged],
        rejected=[
            RejectionView(
                source_conversation_id=item.source_conversation_id,
                title=item.title,
                reason=item.reason,
            )
            for item in plan.rejected
        ],
    )


def _require_a_working_model(worker: IngestWorker) -> None:
    """
    Refuse the upload if nothing could process it.

    Separate from the failure the worker would eventually record, because
    the two mean different things to whoever is holding the file: one is
    "your export is fine, this service is not configured", and the other
    looks like the export is at fault.
    """
    try:
        worker.ensure_ready()
    except ProviderError as exc:
        raise Unavailable("importing", f"no usable model is configured: {exc}") from exc


def _decode(raw: bytes) -> Any:
    """
    Turn uploaded bytes into JSON, or say plainly that they are not JSON.

    Both failures here are the caller's to fix and neither reveals anything
    about the store, so both are repeated back rather than swallowed into a
    generic apology.
    """
    if not raw.strip():
        raise BadRequest("that file is empty")
    try:
        return json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise BadRequest("that file is not text; a chat export is JSON") from exc
    except json.JSONDecodeError as exc:
        raise BadRequest(f"that file is not valid JSON: {exc}") from exc


def _parse(payload: Any, filename: str, config: AppConfig) -> ImportPlan:
    """
    Read the export, turning an unreadable one into a plain refusal.

    The configured time zone is handed in rather than read by the parser,
    which keeps the parser a function of its arguments. It decides one
    thing: which calendar day a conversation belongs to, in the calendar
    the person actually lives in.
    """
    try:
        return parse_export(
            payload, filename=filename, local_timezone=config.ingest.tzinfo()
        )
    except ExportFormatError as exc:
        raise BadRequest(str(exc)) from exc


def _receipt(item: StagedConversation) -> ConversationReceipt:
    """One conversation's identifiers, settled before any work has started."""
    return ConversationReceipt(
        import_id=item.import_id,
        session_id=item.session_id,
        title=item.title,
        event_date=item.event_date.isoformat(),
        message_count=item.message_count,
        already_imported=item.already_imported,
    )


__all__ = ["router"]
