"""
Watching what the product does when nobody is asking it to.

Two ways in, and the difference is what each is for. The socket is for a page
that is open now and wants to see things as they happen. The listing is for a
page that has just opened and wants to know what it missed in the last few
minutes.

Deliberately a different socket from the one a conversation runs on. They have
different lifetimes and their failures mean different things: a dropped reply
stream loses a sentence somebody is waiting for, a dropped event stream loses
a notification. On one socket, the reply stream would have to stay open
between conversations and a notification could arrive in the middle of a
sentence.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

from lumen.api.deps import get_events
from lumen.api.events import EventBus
from lumen.api.schemas import EventListView, EventView

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/events", tags=["events"])

# The most events one request will list. The backlog is short by design and
# this is shorter still — anything worth keeping is readable from the
# endpoint that owns it.
MAX_LIMIT = 100


@router.websocket("/ws")
async def watch(websocket: WebSocket) -> None:
    """
    Everything that happens, from the moment you connect.

    Nothing is replayed and nothing is owed to somebody who was not here.
    A listener that falls too far behind loses its own oldest messages rather
    than holding up whatever published them.
    """
    await websocket.accept()
    bus: EventBus = websocket.app.state.events

    try:
        async for event in bus.listen():
            await websocket.send_json(_as_json(event))
    except WebSocketDisconnect:
        logger.info("something stopped watching")


@router.get("", response_model=EventListView)
def what_just_happened(
    bus: EventBus = Depends(get_events),
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
) -> EventListView:
    """
    The last few things that happened, oldest first.

    For a page that has just opened. This is a short backlog rather than a
    history — a system that kept every event would be keeping a second, worse
    copy of what the graph and the job records already hold.
    """
    events = bus.recent(limit)
    return EventListView(
        events=[EventView(**_as_json(event)) for event in events],
        count=len(events),
        listeners=bus.listeners,
    )


def _as_json(event) -> dict:
    """One event in the shape that goes over the wire."""
    return {
        "kind": event.kind,
        "at": event.at.isoformat(),
        "payload": dict(event.payload),
    }


__all__ = ["router"]
