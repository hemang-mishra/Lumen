"""
Noticing that a conversation has finished, and handing it to the pipeline.

This is the join that was never made. Talking to Lumen stores every turn, and
the pipeline turns a finished conversation into somebody's history — and until
now the only way to get from one to the other was to export the conversation
and upload it back to yourself. The query that finds finished conversations
has existed since the operational database was built and nothing has ever
called it.

A conversation is finished when it has gone quiet for long enough. Not when
somebody says so: people do not end journal entries, they drift away from
them, and waiting for a deliberate ending would mean waiting forever for most
of them.

Two rules keep this safe.

**Only conversations Lumen holds itself.** An imported one sits in the same
table in the same state while the importer is working through it, and two
owners for one conversation is how one evening becomes two sets of history.
Which is which is written on the conversation, so no second lookup is needed.

**Claimed, not chosen.** Ownership is taken in a single conditional write that
the database resolves. Looking and then acting leaves a gap, and the gap is
exactly wide enough for the importer to be in it.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from lumen.config import AppConfig
from lumen.operational.enums import BufferSource
from lumen.operational.repositories import OperationalStore

logger = logging.getLogger(__name__)

# Where a conversation has to have come from for this to own it. Everything
# else belongs to the importer, which runs its own conversations and marks
# them itself.
OURS: frozenset[BufferSource] = frozenset(
    {BufferSource.NATIVE_CHAT, BufferSource.VOICE_NOTE}
)


class DecayedConversationWatcher:
    """
    Hands finished conversations to the pipeline.

    Holds the store it reads and the worker it hands to, and nothing else.
    What the pipeline then does with a conversation is none of its business —
    it is the thing that notices, not the thing that extracts.
    """

    name = "session-decay"

    def __init__(
        self,
        *,
        ops: OperationalStore,
        worker,
        config: AppConfig | None = None,
    ) -> None:
        """
        Args:
            ops: Where conversations live.
            worker: The thing with a queue and a thread that runs pipelines.
                Handed the session and nothing else.
            config: How long counts as quiet, and how many to hand over at
                once.
        """
        self._ops = ops
        self._worker = worker
        self._config = config or AppConfig()

    @property
    def every(self) -> timedelta:
        """How often to look."""
        return timedelta(seconds=self._config.scheduler.watch_every_seconds)

    def run(self, now: datetime) -> int:
        """
        Find the conversations that have gone quiet and dispatch them.

        Returns how many were actually handed over — which is not how many
        were found. Something else may own one, and losing that race is an
        ordinary outcome rather than a problem.
        """
        cutoff = now - timedelta(minutes=self._config.operational.session_decay_minutes)
        waiting = self._ops.buffers.find_decayed(
            cutoff, limit=max(self._config.scheduler.max_dispatch_per_tick, 1)
        )
        if not waiting:
            return 0

        dispatched = 0
        for buffer in waiting:
            if not self._is_ours(buffer):
                continue
            if not self._ops.buffers.claim_for_processing(buffer.session_id, at=now):
                logger.debug(
                    "a finished conversation was already taken",
                    extra={"session_id": buffer.session_id},
                )
                continue
            self._worker.submit_session(buffer.session_id)
            dispatched += 1

        if dispatched:
            logger.info(
                "finished conversations were handed to the pipeline",
                extra={"found": len(waiting), "dispatched": dispatched},
            )
        return dispatched

    def _is_ours(self, buffer) -> bool:
        """
        Whether this conversation is one Lumen held rather than one it was given.

        An imported conversation has an owner already. Taking it would mean
        the same evening running twice, which is worse than it running late.
        """
        if buffer.source in OURS:
            return True
        logger.debug(
            "a finished conversation belongs to the importer",
            extra={"session_id": buffer.session_id, "source": buffer.source.value},
        )
        return False


__all__ = ["DecayedConversationWatcher", "OURS"]
