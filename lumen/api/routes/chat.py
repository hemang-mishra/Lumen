"""
Talking to Lumen, and looking back at what was said.

The conversation itself is a web socket, because a turn is not one answer —
it is a sequence, and the person is watching part of it happen. Sending the
reply as it is written is most of what makes a few seconds of thinking feel
like thinking rather than lag.

Everything else here is an ordinary request: hand in a recording and get
words back, ask for a different reply, rewrite something, or read an earlier
day.

**None of this can reach the graph.** The chat layer writes conversations,
into the same store the extraction pipeline already reads — which is the
whole point, because it means today's talking becomes tomorrow's history with
nothing to copy across. The graph stays reachable for writing only from the
importer's own thread.
"""

from __future__ import annotations

import base64
import logging
from datetime import UTC, date, datetime, timedelta

import anyio
from fastapi import APIRouter, Depends, File, UploadFile, WebSocket
from fastapi.responses import JSONResponse
from starlette.websockets import WebSocketDisconnect

from lumen.api.deps import (
    get_chat_stack,
    get_config,
    get_identity,
    get_memory,
    require_identity,
)
from lumen.api.errors import Unavailable
from lumen.api.schemas import (
    ChatDayView,
    ChatMessageView,
    ChatThreadView,
    ReviseRequest,
    TranscriptView,
)
from lumen.auth import Identity
from lumen.auth.contracts import NotAuthenticated
from lumen.config import AppConfig
from lumen.providers.errors import ProviderError
from lumen.query.chat import VOICE, TurnEvent
from lumen.query.conversation import ConversationFrozen
from lumen.query.memory import ConversationMemory

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

# What a socket is closed with when whoever is on it may no longer be there.
# The code browsers show as a policy refusal rather than a network fault, so
# a client can tell "sign in again" from "try reconnecting".
WS_UNAUTHORISED = 1008

# How often a conversation re-checks who is talking.
#
# A socket stays open for an hour and a token lasts fifteen minutes, so the
# question is where to put the check. Every frame would mean interrupting a
# sentence mid-word to argue about credentials; never would mean a session
# somebody ended carrying on until they close the tab. The turn boundary is
# where the person has stopped and is waiting anyway.
CHECK_EVERY_TURN = True


@router.websocket("/ws")
async def hold_a_conversation(
    websocket: WebSocket,
) -> None:
    """
    Talk, and hear back as the reply is written.

    Each message in is one thing the person said. Each message out is one
    step of the turn — accepted, what was gathered, the reply arriving piece
    by piece, and finally either a finished reply or an honest failure.

    A failure is sent rather than closing the socket. Somebody mid-conversation
    should see what went wrong on this turn and be able to try the next one,
    not find the connection gone.
    """
    await websocket.accept()
    stack = websocket.app.state.chat
    settings: AppConfig = websocket.app.state.config

    try:
        identity = _who_is_talking(websocket)
    except NotAuthenticated as refused:
        # Refused before anything is said rather than after. Somebody whose
        # session has ended should find that out now, not one sentence in.
        await websocket.send_json(
            {"kind": "error", "reason": "not_authenticated", "detail": refused.reason}
        )
        await websocket.close(code=WS_UNAUTHORISED)
        return

    try:
        while True:
            said = await websocket.receive_json()
            try:
                # Checked again at the top of every turn, which is the one
                # moment nobody is mid-sentence. A session ended somewhere
                # else stops here rather than at the end of the hour.
                identity = _who_is_talking(websocket)
            except NotAuthenticated as refused:
                await websocket.send_json(
                    {
                        "kind": "error",
                        "reason": "not_authenticated",
                        "detail": refused.reason,
                    }
                )
                await websocket.close(code=WS_UNAUTHORISED)
                return
            await _one_turn(websocket, stack, settings, said, identity)
    except WebSocketDisconnect:
        logger.info("the conversation was closed by the other end")


async def _one_turn(
    websocket: WebSocket,
    stack,
    settings: AppConfig,
    said: dict,
    identity: Identity,
) -> None:
    """
    Run one turn and send every step of it out as it happens.

    The engine is synchronous and this is not, so the work runs on a worker
    thread — an event loop blocked for the length of a model reply would stop
    every other conversation in the process.
    """
    text = str(said.get("text") or "").strip()
    if not text:
        await websocket.send_json(
            {"kind": "error", "reason": "empty_turn", "detail": "nothing was said"}
        )
        return

    try:
        engine = stack.engine()
    except ProviderError as exc:
        await websocket.send_json(
            {
                "kind": "error",
                "reason": "no_model",
                "detail": f"no conversation model is configured: {exc}",
            }
        )
        return

    speak = bool(said.get("speak")) and settings.chat.voice_enabled
    modality = VOICE if said.get("spoken") else "TEXT"

    events = await anyio.to_thread.run_sync(
        lambda: list(
            engine.say(identity.user_id, text, modality=modality, speak=speak)
        )
    )
    for event in events:
        await websocket.send_json(_as_json(event))


@router.post("/transcribe", response_model=TranscriptView)
async def write_down_what_was_said(
    audio: UploadFile = File(...),
    config: AppConfig = Depends(get_config),
    stack=Depends(get_chat_stack),
) -> TranscriptView:
    """
    Turn a recording into words.

    A separate request rather than bytes on the socket. Audio is a bulk
    upload and the socket is for the back-and-forth; a failed upload should
    not take a conversation down with it. What comes back is sent on as an
    ordinary turn, marked as spoken.
    """
    raw = await audio.read()
    if not raw:
        raise Unavailable("listening", "the recording is empty")
    if len(raw) > config.chat.max_audio_bytes:
        raise Unavailable(
            "listening",
            f"the recording is larger than the {config.chat.max_audio_bytes} "
            f"byte limit",
        )

    try:
        listener = stack.listener()
        heard = listener.transcribe(
            raw, mime_type=audio.content_type or "audio/webm"
        )
    except ProviderError as exc:
        raise Unavailable("listening", str(exc)) from exc

    return TranscriptView(text=heard.text, language=heard.language)


@router.get("/days", response_model=list[ChatDayView])
def which_days_have_conversations(
    identity: Identity = Depends(require_identity),
    memory: ConversationMemory = Depends(get_memory),
    config: AppConfig = Depends(get_config),
) -> list[ChatDayView]:
    """
    The recent days that hold a conversation, newest first.

    Each says whether it can still be changed, because that is the question
    somebody is about to ask and the answer is not guessable from the date.
    """
    today = datetime.now(UTC).date()
    earlier = memory.store.days_before(
        identity.user_id,
        on=today + _one_day(),
        limit=config.chat.previous_days * 10,
        lookback_days=365,
    )
    return [
        ChatDayView(
            session_id=record.session_id,
            event_date=record.event_date,
            message_count=record.message_count,
            status=record.status.value,
            editable=record.status.value == "OPEN",
            summary=record.rolling_summary,
        )
        for record in earlier
    ]


@router.get("/days/{on}", response_model=ChatThreadView)
def read_one_day_back(
    on: date,
    identity: Identity = Depends(require_identity),
    memory: ConversationMemory = Depends(get_memory),
) -> ChatThreadView:
    """
    One earlier day, as the person would read it.

    Branches they moved away from are left out — this is the conversation
    they settled on, which is also the one the pipeline extracted.
    """
    buffer = memory.store.open(identity.user_id, on=on)
    thread = memory.store.thread(buffer.session_id)
    return ChatThreadView(
        session_id=buffer.session_id,
        event_date=buffer.event_date,
        editable=memory.store.is_editable(buffer.session_id),
        summary=buffer.rolling_summary,
        messages=[
            ChatMessageView(
                message_id=item.message_id,
                role=item.turn.role,
                content=item.turn.content,
                timestamp=item.turn.timestamp,
            )
            for item in thread
        ],
    )


@router.post("/messages/{message_id}/revise", response_model=ChatMessageView)
def say_it_differently(
    message_id: str,
    body: ReviseRequest,
    memory: ConversationMemory = Depends(get_memory),
) -> ChatMessageView:
    """
    Rewrite something, while the day is still open.

    The rewrite is stored beside the original rather than over it, and the
    conversation starts reading from the new one. Nothing anybody said is
    destroyed.

    Once a day has been processed it refuses, and says what to do instead.
    An episode is stored under the day it happened on rather than under what
    it says, so a re-run of an edited day would find it already saved and
    skip it — the conversation and the graph would disagree from then on with
    nothing anywhere reporting it.
    """
    try:
        written = memory.store.revise(
            body.session_id, message_id=message_id, content=body.content
        )
    except ConversationFrozen as exc:
        return JSONResponse(
            status_code=409,
            content={
                "error": "conversation_frozen",
                "detail": str(exc),
                "instead": exc.instead,
            },
        )

    return ChatMessageView(
        message_id=written.message_id,
        role=written.turn.role,
        content=written.turn.content,
        timestamp=written.turn.timestamp,
    )


def _as_json(event: TurnEvent) -> dict:
    """
    One event as something a browser can read.

    Audio is the only awkward part: it is bytes, and a socket carrying JSON
    cannot hold them, so it goes as text and is turned back on the other side.
    """
    payload = event.model_dump()
    if isinstance(payload.get("audio"), bytes):
        payload["audio"] = base64.b64encode(payload["audio"]).decode("ascii")
    return payload


def _one_day() -> timedelta:
    """A single day, so "up to and including today" can be asked for."""
    return timedelta(days=1)


def _who_is_talking(websocket: WebSocket) -> Identity:
    """
    Who is on the other end of this socket.

    The router already refuses an unauthenticated connection before this
    handler runs. This is called anyway, and again at every turn, because a
    router dependency cannot hand its answer to a socket and because a
    session ended halfway through an hour-long conversation should stop it.
    """
    # The same resolution every other route uses. A socket cannot take a
    # dependency's return value, so it is called rather than declared — but
    # it is the same rule, in the same place, which is the whole point of
    # having one.
    return get_identity(websocket)


__all__ = ["router", "CHECK_EVERY_TURN", "WS_UNAUTHORISED"]
