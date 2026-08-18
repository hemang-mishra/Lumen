"""
The conversation, as the live side of Lumen touches it.

A narrow view over the store that already holds conversations waiting to be
processed. Narrow on purpose: what the chat layer needs is to add a turn,
read the thread back, and keep a summary — and a component that could do
more than that would eventually do more than that.

It writes, which is new for this side of Lumen. The rule that mattered is
unchanged and worth restating: **the graph is still read-only here.** What
gets written is the conversation itself, into the same buffer the extraction
pipeline already reads, which is the point — a chat held anywhere else would
be a chat that never becomes history.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime

from lumen.operational.enums import BufferSource, BufferStatus
from lumen.operational.repositories import SessionBufferRepository
from lumen.operational.schemas import BufferMessageRecord, SessionBufferRecord
from lumen.schemas.query import ChatTurn

logger = logging.getLogger(__name__)

# How a role is written in the store, and how it is written in a live turn.
# Two vocabularies for one idea, because the store predates the chat layer
# and renaming a stored value is a migration for no gain.
STORED_ROLE = {"user": "USER", "assistant": "AI"}
LIVE_ROLE = {"USER": "user", "AI": "assistant"}

# While a conversation is still being collected it can be rewritten freely.
# Once it has been handed to the extraction pipeline it has become history,
# and history is not edited.
EDITABLE_STATUSES = frozenset({BufferStatus.OPEN})


class ConversationFrozen(Exception):
    """
    An edit arrived for a day that has already become history.

    Worth its own error rather than a plain refusal, because the honest
    answer includes what to do instead. An episode is stored under the day it
    happened on, not under what it says, so re-running an edited day finds
    that day already saved and skips it — the conversation and the graph
    would disagree from then on with nothing anywhere reporting it.

    What the person actually wants is almost always available: say it again
    today. The correction becomes a new entry, and changing your mind is
    something the graph already knows how to record.

    Attributes:
        session_id: The conversation that is closed.
        status: How far along it is.
        instead: The thing to do instead, in plain words.
    """

    def __init__(self, session_id: str, status: BufferStatus) -> None:
        super().__init__(
            f"the conversation on {session_id!r} has already been processed "
            f"({status.value}) and cannot be changed"
        )
        self.session_id = session_id
        self.status = status
        self.instead = "say it again today, as a new message"


@dataclass(frozen=True)
class StoredTurn:
    """
    One turn as the store holds it.

    Attributes:
        message_id: What this message is called, and what a later edit points
            at when it replaces this one.
        parent_message_id: What it was a reply to.
        turn: The turn itself, in the shape the live side uses.
        seq: Arrival order, which is not the same as conversational order
            once anything has been edited.
    """

    message_id: str
    parent_message_id: str | None
    turn: ChatTurn
    seq: int


class ConversationStore:
    """
    Reads and writes one person's conversations.

    Everything is done through the repository it is handed, so this can be
    pointed at a temporary database without changing anything about how it
    works — and so it cannot reach anything the repository does not offer.
    """

    def __init__(self, buffers: SessionBufferRepository) -> None:
        self._buffers = buffers

    # ------------------------------------------------------------------
    # Starting and finding one
    # ------------------------------------------------------------------

    def open(
        self,
        user_id: str,
        *,
        on: date,
        label: str = "",
        source: BufferSource = BufferSource.NATIVE_CHAT,
    ) -> SessionBufferRecord:
        """
        The conversation for this person on this day, started if it is new.

        One per person per day per label, which is the same rule the live
        session follows — so a chat and the record that will later become
        history are the same thing rather than two things that resemble each
        other.
        """
        return self._buffers.find_or_create(
            user_id, on, session_label=label, source=source
        )

    def get(self, session_id: str) -> SessionBufferRecord | None:
        """One conversation's own record, or nothing if there is no such one."""
        return self._buffers.get_buffer(session_id)

    def days_before(
        self,
        user_id: str,
        *,
        on: date,
        limit: int,
        label: str = "",
        lookback_days: int = 14,
    ) -> list[SessionBufferRecord]:
        """
        The last few days this person actually talked, before a given day.

        Days with nothing in them are skipped, so somebody who writes twice a
        week still gets the last few conversations rather than a run of
        blanks.
        """
        return self._buffers.recent_buffers(
            user_id,
            before=on,
            limit=limit,
            session_label=label,
            lookback_days=lookback_days,
        )

    # ------------------------------------------------------------------
    # Saying things
    # ------------------------------------------------------------------

    def append(
        self,
        session_id: str,
        *,
        role: str,
        content: str,
        at: datetime | None = None,
        event_date: date | None = None,
        modality: str = "TEXT",
    ) -> StoredTurn:
        """
        Add a turn to the end of the conversation as it currently reads.

        The reply link is filled in by the store from wherever the
        conversation currently ends, so ordinary talking needs to know
        nothing about branches.

        Whether it was spoken or typed is recorded, because the extraction
        pipeline cleans the two differently and a spoken turn is full of
        things nobody would ever write down.
        """
        return self._write(
            session_id,
            role=role,
            content=content,
            at=at,
            event_date=event_date,
            modality=modality,
        )

    def revise(
        self,
        session_id: str,
        *,
        message_id: str,
        content: str,
        at: datetime | None = None,
    ) -> StoredTurn:
        """
        Say one of the earlier turns differently.

        The rewrite is stored beside the original rather than over it, and
        the conversation starts reading from the new one. Everything that
        followed the original stays exactly where it is and is simply no
        longer the thread being read — nothing anybody said is destroyed,
        which is the same rule the graph lives by.
        """
        self._require_open(session_id)

        original = self._find(session_id, message_id)
        if original is None:
            raise ValueError(
                f"no message {message_id!r} in conversation {session_id!r}"
            )

        written = self._write(
            session_id,
            role=LIVE_ROLE.get(original.role, "user"),
            content=content,
            at=at,
            event_date=original.event_date,
            parent=original.parent_message_id,
            explicit_parent=True,
            modality=original.modality,
        )
        logger.info(
            "a turn was said differently, and the conversation reads from the new one",
            extra={
                "session_id": session_id,
                "replaced": message_id,
                "with": written.message_id,
            },
        )
        return written

    def rewind_to(self, session_id: str, message_id: str) -> None:
        """Read from a branch that had been left behind."""
        self._require_open(session_id)
        self._buffers.set_active(session_id, message_id)

    def is_editable(self, session_id: str) -> bool:
        """Whether this conversation is still open to being changed."""
        buffer = self._buffers.get_buffer(session_id)
        return buffer is not None and buffer.status in EDITABLE_STATUSES

    def _require_open(self, session_id: str) -> None:
        """Refuse to change a day that has already become history."""
        buffer = self._buffers.get_buffer(session_id)
        if buffer is None:
            raise ValueError(f"no conversation {session_id!r}")
        if buffer.status not in EDITABLE_STATUSES:
            raise ConversationFrozen(session_id, buffer.status)

    # ------------------------------------------------------------------
    # Reading it back
    # ------------------------------------------------------------------

    def thread(self, session_id: str) -> list[StoredTurn]:
        """
        The conversation as the person would read it, oldest first.

        Branches they moved away from are left out. They are still stored,
        and nothing here can remove them.
        """
        return [
            StoredTurn(
                message_id=record.message_id,
                parent_message_id=record.parent_message_id,
                turn=_as_turn(record, index),
                seq=record.seq,
            )
            for index, record in enumerate(self._buffers.active_thread(session_id))
        ]

    def remember_summary(self, session_id: str, summary: str, through_seq: int) -> None:
        """Record what the conversation has been about so far."""
        self._buffers.save_summary(session_id, summary, through_seq)

    # ------------------------------------------------------------------
    # Doing the writing
    # ------------------------------------------------------------------

    def _write(
        self,
        session_id: str,
        *,
        role: str,
        content: str,
        at: datetime | None,
        event_date: date | None,
        parent: str | None = None,
        explicit_parent: bool = False,
        modality: str = "TEXT",
    ) -> StoredTurn:
        """Store one message, wherever in the conversation it belongs."""
        buffer = self._buffers.get_buffer(session_id)
        if buffer is None:
            raise ValueError(f"no conversation {session_id!r}")

        moment = at or datetime.now(UTC)
        record = BufferMessageRecord(
            message_id=f"msg_{uuid.uuid4().hex[:16]}",
            session_id=session_id,
            # Arrival order, which every message has and no two share. Where
            # a message sits in the conversation is the reply link's job.
            seq=self._next_seq(session_id),
            role=STORED_ROLE.get(role, "USER"),
            content=content,
            timestamp=moment,
            event_date=event_date or buffer.event_date,
            parent_message_id=parent,
            modality=modality,
        )

        if explicit_parent:
            self._buffers.branch_from(session_id, parent, record)
        else:
            self._buffers.append_message(session_id, record)

        return StoredTurn(
            message_id=record.message_id,
            parent_message_id=parent,
            turn=_as_turn(record, self._depth(session_id)),
            seq=record.seq,
        )

    def _next_seq(self, session_id: str) -> int:
        """The next arrival number, which never repeats within a conversation."""
        held = self._buffers.get_messages(session_id)
        return max((record.seq for record in held), default=-1) + 1

    def _depth(self, session_id: str) -> int:
        """How many turns into the conversation the newest message sits."""
        return max(len(self._buffers.active_thread(session_id)) - 1, 0)

    def _find(self, session_id: str, message_id: str) -> BufferMessageRecord | None:
        """One stored message by name, looking through branches as well."""
        return next(
            (
                record
                for record in self._buffers.get_messages(session_id)
                if record.message_id == message_id
            ),
            None,
        )


def _as_turn(record: BufferMessageRecord, index: int) -> ChatTurn:
    """
    A stored message in the shape the live side uses.

    The position comes from where the message sits in the thread rather than
    from its arrival number, because once something has been edited those
    two differ — and what the assistant is shown has to be the conversation
    as the person reads it.
    """
    return ChatTurn(
        turn_index=index,
        role=LIVE_ROLE.get(record.role, "user"),
        content=record.content,
        timestamp=record.timestamp,
    )


__all__ = [
    "ConversationStore",
    "ConversationFrozen",
    "StoredTurn",
    "STORED_ROLE",
    "LIVE_ROLE",
    "EDITABLE_STATUSES",
]
