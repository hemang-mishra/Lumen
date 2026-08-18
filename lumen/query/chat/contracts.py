"""
What happens during one turn, said out loud as it happens.

A turn is not one thing that finishes — it is a sequence, and the person is
watching part of it. So the engine hands back a stream of small events rather
than a finished object, and whoever is driving decides what to do with each.

Three quite different things consume these. A web socket forwards them to a
browser. A command line prints them. A test collects them and checks the
order. All three want the same sequence, which is why it is a sequence of
plain models rather than callbacks.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TurnEvent(BaseModel):
    """
    Something that happened during a turn.

    Every event names its own kind, so a reader can tell them apart without
    knowing the class names — which is what makes them safe to send over a
    socket as plain JSON.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str


class TurnAccepted(TurnEvent):
    """
    What the person said has been stored, before anything else is attempted.

    First on purpose. Everything after this can fail, and their own words
    should survive it — a turn that dies while the model is writing must not
    also lose the sentence that started it.
    """

    kind: str = "turn.accepted"
    session_id: str
    message_id: str
    turn_index: int = Field(ge=0)


class ContextReady(TurnEvent):
    """
    What was decided and fetched, before the reply starts.

    None of this is meant for the person — the whole point of the layer is
    that it is invisible. It is here so that somebody building or debugging
    can see what the assistant was given, which is otherwise unknowable from
    the outside.
    """

    kind: str = "context.ready"
    emotional_register: str
    triggers: tuple[str, ...] = ()
    briefing: tuple[str, ...] = ()
    carried_forward: tuple[str, ...] = ()
    withheld: tuple[str, ...] = ()
    search_failed: bool = False
    previous_days: int = Field(default=0, ge=0)
    formulation_ms: int = Field(default=0, ge=0)
    retrieval_ms: int = Field(default=0, ge=0)
    prompt_tokens: int = Field(default=0, ge=0)


class ReplyDelta(TurnEvent):
    """One piece of the reply, as it is written."""

    kind: str = "reply.delta"
    text: str


class ReplyDone(TurnEvent):
    """
    The reply finished and was stored.

    Carries the whole text as well as the pieces that came before it, because
    a consumer that only wanted the finished answer should not have to
    reassemble it.
    """

    kind: str = "reply.done"
    message_id: str
    text: str
    first_chunk_ms: int = Field(default=0, ge=0)
    elapsed_ms: int = Field(default=0, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)


class SpokenReply(TurnEvent):
    """
    The reply as something to listen to.

    Sent after the words rather than alongside them. Saying each sentence as
    it appears would start the audio sooner, and it needs sentence-splitting
    on a live stream and gapless playback of many small clips — worth doing
    later, not worth doing first.
    """

    kind: str = "audio.reply"
    audio: bytes
    mime_type: str = "audio/wav"


class TurnFailed(TurnEvent):
    """
    Something went wrong, and how much of the reply had already been said.

    `said` matters. A break partway through leaves words on the screen that
    cannot be taken back, and a consumer needs to know whether it is showing
    half an answer or none of one.
    """

    kind: str = "error"
    reason: str
    detail: str = ""
    said: str = ""


__all__ = [
    "TurnEvent",
    "TurnAccepted",
    "ContextReady",
    "ReplyDelta",
    "ReplyDone",
    "SpokenReply",
    "TurnFailed",
]
