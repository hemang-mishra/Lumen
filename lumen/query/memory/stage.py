"""
Keeping hold of a long conversation.

An hour into talking, the beginning is out of reach. Sending every turn
costs more each time somebody says something and eventually stops being
possible; sending only the last few loses the thing they came in with, which
is usually the thing that matters.

So both. The recent turns go word for word, because those are what they are
actually responding to. Everything older is folded into a few sentences
about what the conversation has been about, refreshed every so often and
written down — and each refresh folds the previous summary in rather than
re-reading the whole conversation, which is what keeps a long chat costing
the same as a short one.

The refresh is deliberately not part of building a prompt. It is a model
call, and nobody should wait on it to get an answer: it happens after a
reply has gone out, ready for the turn after next.
"""

from __future__ import annotations

import logging

from lumen.config import ChatConfig
from lumen.providers.errors import ProviderError
from lumen.providers.protocols import LLMProvider
from lumen.query.conversation import ConversationStore, StoredTurn
from lumen.providers.protocols import ChatMessage
from lumen.query.memory import prompts
from lumen.query.memory.contracts import Recollection

logger = logging.getLogger(__name__)


class ConversationMemory:
    """
    What the assistant can still see of the conversation.

    Holds nothing itself. The conversation lives in the store and the summary
    lives with it, so two processes reading the same chat see the same thing
    and a restart loses nothing.
    """

    def __init__(
        self,
        *,
        store: ConversationStore,
        llm: LLMProvider | None = None,
        config: ChatConfig | None = None,
    ) -> None:
        """
        Args:
            store: Where conversations live.
            llm: The model that writes the summary. Optional, because
                everything except refreshing works without one — a
                deployment with no model still holds a conversation, it just
                stops compressing the old part of it.
            config: How many turns to keep, and how often to summarise.
        """
        self._store = store
        self._llm = llm
        self._config = config or ChatConfig()

    @property
    def store(self) -> ConversationStore:
        """
        Where the conversation lives.

        Exposed because saying something and remembering it are two halves of
        one job, and a caller that has this should not need a second handle
        to the same conversation to do the other half.
        """
        return self._store

    def recall(self, session_id: str) -> Recollection:
        """
        The conversation as the assistant should see it now.

        Cheap: two reads and no model call. This runs on every turn, so it
        has to be.

        A conversation nobody has started yet comes back empty rather than
        failing. "This is the first thing anybody has said" is an ordinary
        state of a chat, not an error.
        """
        buffer = self._store.get(session_id)
        if buffer is None:
            return Recollection()

        thread = self._store.thread(session_id)
        summary = buffer.rolling_summary or None
        through = buffer.summary_through_seq

        keep = max(self._config.recent_turns, 1)
        recent = thread[-keep:]

        # The summary is sent only when there is conversation it covers that
        # the recent turns do not. A short chat that fits entirely inside the
        # window needs no summary of itself, and sending one would have the
        # assistant read the same stretch twice in two different voices.
        return Recollection(
            summary=summary if len(thread) > len(recent) else None,
            turns=tuple(item.turn for item in recent),
            summarised_through=through,
            total_turns=len(thread),
        )

    def refresh(self, session_id: str, *, force: bool = False) -> bool:
        """
        Fold everything older than the recent window into the summary.

        Returns whether it wrote one. Called after a reply has gone out, so
        the cost lands where nobody is waiting — and skipped when there is
        not enough new material to be worth a call, which is most turns.

        A failure here costs the conversation nothing today: the old summary
        stays, the recent turns are still sent word for word, and the next
        refresh tries again.
        """
        buffer = self._store.get(session_id)
        if buffer is None:
            logger.debug(
                "asked to write up a conversation that does not exist",
                extra={"session_id": session_id},
            )
            return False

        thread = self._store.thread(session_id)
        keep = max(self._config.recent_turns, 1)
        older = thread[:-keep] if len(thread) > keep else []
        unsummarised = [item for item in older if item.seq > buffer.summary_through_seq]

        if not unsummarised:
            return False
        if not force and len(unsummarised) < max(self._config.summary_every, 1):
            return False
        if self._llm is None:
            logger.debug(
                "no model is configured, so the conversation is not being summarised",
                extra={"session_id": session_id},
            )
            return False

        written = self._write_summary(buffer.rolling_summary, unsummarised)
        if written is None:
            return False

        self._store.remember_summary(session_id, written, unsummarised[-1].seq)
        logger.info(
            "the conversation so far was written up",
            extra={
                "session_id": session_id,
                "folded_in": len(unsummarised),
                "through_seq": unsummarised[-1].seq,
            },
        )
        return True

    def _write_summary(
        self, previous: str | None, turns: list[StoredTurn]
    ) -> str | None:
        """
        Ask the model to merge what came before with what has been said since.

        Nothing is raised. A summary that could not be written is a
        conversation that remembers slightly less, which is a much smaller
        problem than a turn that failed.
        """
        rendered = prompts.render_turns(
            [
                ("THEM" if item.turn.role == "user" else "YOU", item.turn.content)
                for item in turns
            ]
        )
        prompt = prompts.build_prompt(
            previous, rendered, word_limit=self._config.summary_words
        )

        try:
            result = self._llm.generate_text(
                [ChatMessage(role="user", content=prompt)],
                system_instruction=prompts.SYSTEM_INSTRUCTION,
            )
        except ProviderError as exc:
            logger.warning(
                "the conversation could not be written up, so the older "
                "summary stands",
                extra={"reason": type(exc).__name__},
            )
            return None

        text = (result.text or "").strip()
        if not text:
            logger.warning("the summary came back empty and was not stored")
            return None
        return text



__all__ = ["ConversationMemory"]
