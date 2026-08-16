"""
Putting an understood file into the waiting room.

Everything here writes, and it writes through exactly the doors a live
conversation uses: a buffer is opened, messages are appended to it, and it is
marked as gone quiet. There is no second way into a session buffer, so an
imported conversation and a typed one are indistinguishable to every stage
that comes afterwards — which is the only arrangement in which testing the
pipeline on imported history proves anything about the real thing.

Two rules shape the file.

A conversation is staged whole or not at all. Its buffer, its messages and
its history row go in one transaction, because a buffer holding half of an
evening would be processed as though that were all the person said.

A conversation that has been imported before is not staged again. The check
runs here, before a model is ever reached, so a second upload of the same
export costs nothing rather than costing a second run over somebody's
history.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from lumen.ingest.contracts import ImportPlan, ParsedConversation, StagedConversation
from lumen.operational.enums import BufferSource, BufferStatus, ImportStatus
from lumen.operational.repositories import OperationalStore
from lumen.operational.schemas import BufferMessageRecord, ImportRecord

logger = logging.getLogger(__name__)

# The session_label column is 64 characters wide, and an exported title can
# be a whole sentence. Cutting it here rather than letting the database
# refuse the row keeps a long title from failing an otherwise fine import.
MAX_LABEL = 64

# What a conversation with no title of its own is filed under.
DEFAULT_LABEL = "imported"


def stage_conversations(
    plan: ImportPlan,
    *,
    ops: OperationalStore,
    user_id: str,
    batch_id: str = "",
) -> list[StagedConversation]:
    """
    Write everything an uploaded file contained into the waiting room.

    Args:
        plan: What the file was understood to hold.
        ops: The operational store to write through.
        user_id: Whose history this is.
        batch_id: Ties every conversation from this upload together. Made up
            here when the caller does not supply one.

    Returns:
        One entry per conversation, in the order they appeared in the file,
        each either newly staged or recognised from an earlier upload. The
        identifiers are settled before any work starts, which is what lets
        the caller be handed something to follow immediately.
    """
    batch = batch_id or f"batch_{uuid.uuid4().hex[:12]}"

    staged = [
        _stage_one(conversation, ops=ops, user_id=user_id, batch_id=batch, plan=plan)
        for conversation in plan.conversations
    ]

    logger.info(
        "staged an upload",
        extra={
            "batch_id": batch,
            "source_file": plan.filename,
            "conversations": len(staged),
            "already_imported": sum(1 for item in staged if item.already_imported),
        },
    )
    return staged


def _stage_one(
    conversation: ParsedConversation,
    *,
    ops: OperationalStore,
    user_id: str,
    batch_id: str,
    plan: ImportPlan,
) -> StagedConversation:
    """
    Write one conversation into the waiting room, unless it is already there.

    The repeat check comes first and costs one indexed lookup. Skipping it
    would mean discovering the repeat only when the database refused the
    history row — by which point the messages have already been written and
    a second run is already queued.
    """
    existing = ops.imports.find_by_conversation(
        user_id, conversation.source_conversation_id
    )
    if existing is not None:
        logger.info(
            "conversation was imported before, so nothing was queued",
            extra={
                "source_conversation_id": conversation.source_conversation_id,
                "import_id": existing.import_id,
                "original_batch_id": existing.batch_id,
            },
        )
        return StagedConversation(
            import_id=existing.import_id,
            session_id=existing.session_id or "",
            source_conversation_id=conversation.source_conversation_id,
            title=conversation.title,
            event_date=conversation.event_date,
            message_count=existing.message_count,
            already_imported=True,
        )

    import_id = f"imp_{uuid.uuid4().hex[:12]}"

    with ops.transaction():
        buffer = _open_buffer(conversation, ops=ops, user_id=user_id)

        for seq, message in enumerate(conversation.messages):
            ops.buffers.append_message(
                buffer.session_id,
                BufferMessageRecord(
                    # Prefixed with the buffer, because message ids are the
                    # primary key across every buffer there has ever been
                    # and two exports from different applications have no
                    # reason to have avoided each other's identifiers.
                    message_id=f"{buffer.session_id}:{message.message_id}"[:128],
                    session_id=buffer.session_id,
                    seq=seq,
                    role=message.role,
                    content=message.content,
                    timestamp=message.timestamp,
                    # One date for the whole conversation, including the part
                    # of it that ran past midnight.
                    event_date=conversation.event_date,
                ),
            )

        # An imported conversation is finished by definition — nobody is
        # going to add to a file that has already been exported — so it goes
        # straight to the state a live conversation reaches only after
        # sitting quiet for two hours.
        ops.buffers.mark_status(buffer.session_id, BufferStatus.DECAYED)

        ops.imports.record(
            ImportRecord(
                import_id=import_id,
                batch_id=batch_id,
                user_id=user_id,
                source_conversation_id=conversation.source_conversation_id,
                title=conversation.title,
                filename=plan.filename,
                event_date=conversation.event_date,
                message_count=len(conversation.messages),
                session_id=buffer.session_id,
                status=ImportStatus.QUEUED,
                created_at=datetime.now(UTC),
            )
        )

    return StagedConversation(
        import_id=import_id,
        session_id=buffer.session_id,
        source_conversation_id=conversation.source_conversation_id,
        title=conversation.title,
        event_date=conversation.event_date,
        message_count=len(conversation.messages),
    )


def _open_buffer(
    conversation: ParsedConversation, *, ops: OperationalStore, user_id: str
):
    """
    Get a buffer for this conversation, never one that is already in use.

    Buffers are keyed by day and label, which is right for a live
    conversation and not quite enough for an imported one: two different
    exported conversations can share a day and a title, and appending the
    second to the first would merge two separate pieces of thinking into
    one entry — or worse, add to a buffer the pipeline has already
    processed, so that the finished half gets read a second time.

    So a buffer that is already occupied is not reused. The fallback label
    is derived from the conversation's own identifier rather than from a
    counter, which keeps it stable: the same conversation always lands in
    the same place.
    """
    label = (conversation.title.strip() or DEFAULT_LABEL)[:MAX_LABEL]

    buffer = ops.buffers.find_or_create(
        user_id,
        conversation.event_date,
        session_label=label,
        source=BufferSource.IMPORT_JSON,
    )
    if buffer.message_count == 0 and buffer.status is BufferStatus.OPEN:
        return buffer

    suffix = conversation.source_conversation_id[-8:]
    distinct = f"{label[: MAX_LABEL - len(suffix) - 1]}-{suffix}"
    logger.info(
        "a buffer for this day and title was already in use, so this "
        "conversation was given one of its own",
        extra={"event_date": str(conversation.event_date), "session_label": distinct},
    )
    return ops.buffers.find_or_create(
        user_id,
        conversation.event_date,
        session_label=distinct,
        source=BufferSource.IMPORT_JSON,
    )


__all__ = ["stage_conversations"]
