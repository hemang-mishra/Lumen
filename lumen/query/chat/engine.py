"""
Holding one turn of a conversation, from what was said to what was answered.

Everything this needs already existed and none of it was joined up. Reading
the turn, fetching the history, building the prompt and remembering the
conversation were each finished pieces with nothing calling them in order.
This is that order, and nothing else — it makes no decisions of its own, and
every piece it uses is handed to it.

The sequence, and why it is this way round:

  1. Open the day. Crossing midnight starts a fresh one and writes up the
     day that just ended, so tomorrow can open knowing about it.
  2. Store what the person said, before anything that can fail.
  3. Read the turn, under its own deadline.
  4. Fetch what it points at, under a shared one.
  5. Recall the conversation, including the last few days.
  6. Build the prompt.
  7. Write the reply, streaming it as it comes.
  8. Store the reply.
  9. Afterwards, when nobody is waiting, fold older turns into the summary.

Steps 2 and 8 bracket everything that can go wrong, so a turn that breaks
halfway still leaves both halves of the conversation that did happen.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, date, datetime

from lumen.config import ChatConfig
from lumen.providers.errors import ProviderError, StreamInterrupted
from lumen.providers.protocols import (
    ChatMessage,
    StreamingLLMProvider,
    TTSProvider,
)
from lumen.query.chat.contracts import (
    ContextReady,
    ReplyDelta,
    ReplyDone,
    SpokenReply,
    TurnAccepted,
    TurnEvent,
    TurnFailed,
)
from lumen.query.formulation import QueryFormulator
from lumen.query.memory import ConversationMemory
from lumen.query.memory.contracts import Recollection
from lumen.query.prompting import ChatPrompt, PromptComposer
from lumen.query.retrieval import ConversationalRetriever
from lumen.query.session import ChatSession, SessionRegistry
from lumen.schemas.query import ChatTurn, RetrievalSignal

logger = logging.getLogger(__name__)

# How a turn arrived. Kept because the extraction pipeline cleans a spoken
# entry differently from a typed one, and it has never had anything to read.
TEXT = "TEXT"
VOICE = "VOICE"


class ChatEngine:
    """
    Runs one turn of a conversation.

    Owns nothing and decides nothing. Every collaborator is injected, which is
    what lets the same object be driven by a web socket, by a command line, or
    by a test with scripted models and temporary databases.

    The voice is optional and separate. A deployment with no speech model
    configured still holds a perfectly good typed conversation, and nothing
    here has to know the difference.
    """

    def __init__(
        self,
        *,
        formulator: QueryFormulator,
        retriever: ConversationalRetriever,
        composer: PromptComposer,
        memory: ConversationMemory,
        sessions: SessionRegistry,
        llm: StreamingLLMProvider,
        speech: TTSProvider | None = None,
        config: ChatConfig | None = None,
    ) -> None:
        self._formulator = formulator
        self._retriever = retriever
        self._composer = composer
        self._memory = memory
        self._sessions = sessions
        self._llm = llm
        self._speech = speech
        self._config = config or ChatConfig()

    # ------------------------------------------------------------------
    # Saying something
    # ------------------------------------------------------------------

    def say(
        self,
        user_id: str,
        text: str,
        *,
        at: datetime | None = None,
        modality: str = TEXT,
        speak: bool = False,
    ) -> Iterator[TurnEvent]:
        """
        Take one thing the person said and answer it.

        Events come back as they happen rather than all at the end, because
        the reply is the slow part and watching it appear is most of what
        makes the wait bearable.

        Nothing is done until the caller starts reading. A turn that is
        prepared and then dropped costs nothing.
        """
        said = text.strip()
        if not said:
            raise ValueError("there is nothing to say")
        return self._run(user_id, said, at or _now(), modality, speak)

    def _run(
        self,
        user_id: str,
        text: str,
        at: datetime,
        modality: str,
        speak: bool,
    ) -> Iterator[TurnEvent]:
        """Do the turn, in order, reporting as it goes."""
        session = self._open_day(user_id, at)
        conversation = self._memory.store.open(user_id, on=session.event_date)

        turn = ChatTurn(
            turn_index=session.next_turn_index(),
            role="user",
            content=text,
            timestamp=at,
        )
        stored = self._memory.store.append(
            conversation.session_id,
            role="user",
            content=text,
            at=at,
            modality=modality,
        )
        yield TurnAccepted(
            session_id=conversation.session_id,
            message_id=stored.message_id,
            turn_index=turn.turn_index,
        )

        signal = self._formulator.formulate(turn, session)
        bundle = self._retriever.retrieve(signal, session)
        recollection = self._memory.recall(conversation.session_id)
        prompt = self._composer.compose(
            bundle=bundle, signal=signal, recollection=recollection, now=at
        )

        yield _what_was_gathered(signal, bundle, prompt, recollection)

        yield from self._answer(conversation.session_id, prompt, speak=speak)

        self._tidy_up(conversation.session_id)

    # ------------------------------------------------------------------
    # Writing the reply
    # ------------------------------------------------------------------

    def _answer(
        self, session_id: str, prompt: ChatPrompt, *, speak: bool
    ) -> Iterator[TurnEvent]:
        """
        Write the reply and store it, whether or not it finishes.

        A reply that breaks partway is still stored. The person read those
        words; pretending they were never said would leave the conversation
        disagreeing with what is on their screen, and the next turn would be
        answered against a history missing half of it.
        """
        said: list[str] = []
        final = None

        try:
            for chunk in self._llm.stream_text(
                _as_messages(prompt), system_instruction=prompt.system
            ):
                if chunk.final:
                    final = chunk
                    continue
                said.append(chunk.text)
                yield ReplyDelta(text=chunk.text)
        except StreamInterrupted as exc:
            yield from self._keep_what_was_said(session_id, exc.said or "".join(said))
            yield TurnFailed(
                reason="reply_interrupted",
                detail=str(exc),
                said=exc.said or "".join(said),
            )
            return
        except ProviderError as exc:
            yield TurnFailed(reason="reply_failed", detail=str(exc))
            return

        whole = "".join(said).strip()
        if not whole:
            yield TurnFailed(reason="empty_reply", detail="the model said nothing")
            return

        written = self._memory.store.append(
            session_id, role="assistant", content=whole
        )
        yield ReplyDone(
            message_id=written.message_id,
            text=whole,
            first_chunk_ms=final.first_chunk_ms if final else 0,
            elapsed_ms=final.elapsed_ms if final else 0,
            completion_tokens=final.usage.completion_tokens if final else None,
        )

        if speak:
            spoken = self._say_it_out_loud(whole)
            if spoken is not None:
                yield spoken

    def _keep_what_was_said(
        self, session_id: str, said: str
    ) -> Iterator[TurnEvent]:
        """Store a reply that broke off, so the conversation still matches."""
        if not said.strip():
            return
        self._memory.store.append(
            session_id, role="assistant", content=said.strip()
        )
        return
        yield  # pragma: no cover - makes this a generator

    def _say_it_out_loud(self, reply: str) -> SpokenReply | None:
        """
        Turn the finished reply into something to listen to.

        A failure here loses the audio and nothing else. The words are
        already written, already stored, and already on the person's screen —
        the voice is the one part of a turn that can be missing without the
        turn having failed.
        """
        if self._speech is None:
            logger.info("no voice is configured, so the reply was not spoken")
            return None

        try:
            spoken = self._speech.synthesize(reply)
        except Exception:
            logger.warning(
                "the reply could not be spoken, so it stays written", exc_info=True
            )
            return None
        return SpokenReply(audio=spoken.audio, mime_type=spoken.mime_type)

    # ------------------------------------------------------------------
    # The day, and what happens between days
    # ------------------------------------------------------------------

    def _open_day(self, user_id: str, at: datetime) -> ChatSession:
        """
        Today's session, writing up yesterday's if the day has turned over.

        The write-up is forced rather than left to the usual cadence. A short
        conversation never accumulates enough turns to trigger one on its
        own, and a day with no summary carries nothing into tomorrow — which
        would make the continuity work only for the days somebody talked a
        lot, which is backwards.
        """
        held = self._sessions.get_for(user_id)
        session = self._sessions.open(user_id, at=at)

        if held is not None and held.event_date != session.event_date:
            self._write_up(user_id, held.event_date)
        return session

    def _write_up(self, user_id: str, on: date) -> None:
        """
        Fold whatever is left of a finished day into its summary.

        Nobody is waiting on this — the day is over — and a failure costs
        tomorrow its sense of what yesterday was about and nothing else.
        """
        try:
            closed = self._memory.store.open(user_id, on=on)
            self._memory.refresh(closed.session_id, force=True)
        except Exception:
            logger.warning(
                "the day that just ended could not be written up",
                exc_info=True,
                extra={"user_id": user_id, "event_date": on.isoformat()},
            )

    def _tidy_up(self, session_id: str) -> None:
        """
        Fold older turns into the summary, now that the reply has gone out.

        Deliberately after the answer. It is a model call, and nobody should
        wait on it to be replied to — it is preparing for the turn after next.
        """
        try:
            self._memory.refresh(session_id)
        except Exception:
            logger.warning(
                "the conversation could not be written up, so the older "
                "summary stands",
                exc_info=True,
                extra={"session_id": session_id},
            )


def _as_messages(prompt: ChatPrompt) -> list[ChatMessage]:
    """The conversation in the shape a model provider takes."""
    return [
        ChatMessage(role=turn.role, content=turn.content)
        for turn in prompt.messages
    ] or [ChatMessage(role="user", content="")]


def _what_was_gathered(
    signal: RetrievalSignal,
    bundle,
    prompt: ChatPrompt,
    recollection: Recollection,
) -> ContextReady:
    """Everything the assistant was given, for anybody who wants to look."""
    return ContextReady(
        emotional_register=signal.emotional_register.value,
        triggers=tuple(kind.value for kind in signal.trigger_types),
        briefing=tuple(item.text for item in prompt.context.items),
        carried_forward=bundle.carried_forward,
        withheld=bundle.gated,
        search_failed=bundle.search_failed,
        previous_days=len(recollection.previous_days),
        formulation_ms=signal.latency_ms,
        retrieval_ms=bundle.latency_ms,
        prompt_tokens=prompt.estimated_tokens,
    )


def _now() -> datetime:
    """The current time, always with a timezone on it."""
    return datetime.now(UTC)


__all__ = ["ChatEngine", "TEXT", "VOICE"]
