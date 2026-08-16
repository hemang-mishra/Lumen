"""
Looking at what the system makes of a sentence.

Everything the live conversation layer decides is invisible by design — it
happens between somebody speaking and the AI answering, and nothing about it
ever reaches a screen. That is right for the product and unworkable for
anybody trying to tell whether it works.

So there is one way in: paste a sentence, get back exactly what would have
been decided about it. It answers the only question that cannot be answered
from the logs alone, which is whether the reading is any good.

It is a POST rather than a GET even though it changes nothing. What is being
sent is somebody's sentence about their own life, and a GET would put that
in the URL, where it lands in every access log and browser history between
here and the server.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends

from lumen.api.deps import get_formulator
from lumen.api.schemas import FormulationRequest
from lumen.query.formulation import QueryFormulator
from lumen.query.session import ChatSession, make_session_id
from lumen.schemas.query import ChatTurn, RetrievalSignal

router = APIRouter(prefix="/query", tags=["query"])

# The conversation this surface pretends to be part of. Every request builds
# its own, so two callers can never see each other's turns and nothing is
# carried between calls.
DEBUG_USER = "debug"


@router.post("/formulate", response_model=RetrievalSignal)
def formulate_turn(
    body: FormulationRequest,
    formulator: QueryFormulator = Depends(get_formulator),
) -> RetrievalSignal:
    """
    Read one sentence and report what would be looked up because of it.

    Nothing is searched for and nothing is stored. The answer is the
    decision only: which reasons survived being checked against the graph,
    how the person sounds, and how the reading was arrived at — a real model
    call, a shortcut, or a failure.
    """
    session = _throwaway_session()
    for index, earlier in enumerate(body.history):
        session.record_turn(
            ChatTurn(
                turn_index=index,
                role=earlier.role,
                content=earlier.content,
                timestamp=_now(),
            )
        )

    turn = ChatTurn(
        turn_index=session.next_turn_index(),
        role="user",
        content=body.text,
        timestamp=_now(),
    )
    return formulator.formulate(turn, session)


def _throwaway_session() -> ChatSession:
    """A conversation that exists for one request and is never held onto."""
    today = _now()
    return ChatSession(
        session_id=make_session_id(DEBUG_USER, today.date()),
        user_id=DEBUG_USER,
        event_date=today.date(),
        created_at=today,
        last_activity_at=today,
    )


def _now() -> datetime:
    """The current time, always with a timezone on it."""
    return datetime.now(UTC)


__all__ = ["router"]
