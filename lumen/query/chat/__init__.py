"""
Holding a conversation.

Everything else in this package prepares for a turn — reading it, fetching
what it points at, building what the assistant is sent. This is the part that
actually has the conversation: it puts those in order, writes the reply, and
stores both halves so today becomes tomorrow's history.
"""

from lumen.query.chat.contracts import (
    ContextReady,
    ReplyDelta,
    ReplyDone,
    SpokenReply,
    TurnAccepted,
    TurnEvent,
    TurnFailed,
)
from lumen.query.chat.engine import TEXT, VOICE, ChatEngine

__all__ = [
    "ChatEngine",
    "TEXT",
    "VOICE",
    "TurnEvent",
    "TurnAccepted",
    "ContextReady",
    "ReplyDelta",
    "ReplyDone",
    "SpokenReply",
    "TurnFailed",
]
