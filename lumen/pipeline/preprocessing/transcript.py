"""
The parts of preprocessing that need no language model.

Everything here is plain text handling: turning a list of buffered messages
into something readable, counting words, hashing, and deciding whether there
is anything left worth looking at. All of it is deterministic, so the
answers are the same every run and can be checked exactly in a test.

The gate that throws input away lives here on purpose. Deciding that
somebody's writing is worthless is the one call in this stage that must not
be left to a model's opinion — it is made by counting, not by judging.
"""

from __future__ import annotations

import hashlib
import logging

from lumen.schemas.enums import DialogueAct, SourceModality
from lumen.schemas.pipeline import BufferMessage, SessionDecayEvent

logger = logging.getLogger(__name__)

# Written by the person journalling, as opposed to the assistant replying.
USER_ROLE = "USER"
AI_ROLE = "AI"


def is_chat_buffer(event: SessionDecayEvent) -> bool:
    """
    Say whether this session is a back-and-forth conversation.

    A conversation is recognised by the assistant having spoken in it. A
    voice note or a pasted journal entry has only the person's own words, so
    there is nothing to untangle and the conversation handling is skipped.

    This reads the messages rather than a flag, so nothing has to be set
    correctly upstream for it to work.
    """
    return any(message.role == AI_ROLE for message in event.raw_buffer)


def user_messages(messages: list[BufferMessage]) -> list[BufferMessage]:
    """
    Keep only what the person actually said.

    Assistant replies are dropped before extraction. They are the
    assistant's words, and treating them as the person's own would put
    things into their history that they never thought.
    """
    return [message for message in messages if message.role == USER_ROLE]


def render_monologue(messages: list[BufferMessage]) -> str:
    """
    Join a person's messages into one continuous piece of writing.

    Used when there is no conversation to untangle — a voice note, or an
    entry typed in one sitting. Blank messages are skipped so they do not
    leave gaps.
    """
    parts = [message.content.strip() for message in messages if message.content.strip()]
    return "\n\n".join(parts)


def render_dialogue(messages: list[BufferMessage]) -> str:
    """
    Lay a conversation out as a labelled script.

    Each line is tagged with who spoke and with the message's id. The id is
    included because the conversation pass has to report a verdict per
    message, and it needs a way to name them.
    """
    lines = []
    for message in messages:
        content = message.content.strip()
        if not content:
            continue
        lines.append(f"[{message.message_id}] {message.role}: {content}")
    return "\n".join(lines)


def word_count(text: str) -> int:
    """
    Count the words in a piece of text.

    Always run on cleaned text, never on the raw input. Forty words of
    speech full of "um" and false starts can be twenty-two real ones, and
    it is the twenty-two that decide how much attention the entry earns.
    """
    return len(text.split())


def text_hash(text: str) -> str:
    """
    Produce a short, stable fingerprint of a piece of text.

    Used to spot the same content arriving twice. The same text always
    gives the same fingerprint, on any machine and in any run.
    """
    return hashlib.blake2b(text.encode("utf-8"), digest_size=16).hexdigest()


def all_turns_operational(turn_acts: dict[str, DialogueAct]) -> bool:
    """
    Say whether every message the person wrote was a request rather than a
    reflection.

    Someone asking "what did I say last Tuesday?" is using the system, not
    confiding in it. A session containing nothing but that has no inner life
    to record.

    An empty mapping returns False. Knowing nothing about the messages is
    not the same as knowing they were all requests, and the difference
    matters when it decides whether the session is kept.
    """
    if not turn_acts:
        return False
    return all(act == DialogueAct.OPERATIONAL_REQUEST for act in turn_acts.values())


def has_extractable_text(text: str) -> bool:
    """Say whether anything survived cleaning besides whitespace."""
    return bool(text.strip())


def warn_on_multi_date(event: SessionDecayEvent) -> None:
    """
    Complain when one session covers several different days.

    Messages carry the day they belong to, and normally they all agree.
    Imported chat logs sometimes do not, and splitting them apart is the
    job of whatever creates the session, not this stage. Rather than
    quietly treating a month of history as one day, this says so, names the
    days it found, and carries on with the session's own date.
    """
    dates = {message.event_date for message in event.raw_buffer}
    if len(dates) <= 1:
        return
    logger.warning(
        "session buffer spans multiple dates; processing all of it under the "
        "session's own date",
        extra={
            "session_id": event.session_id,
            "session_event_date": event.event_date.isoformat(),
            "message_event_dates": sorted(day.isoformat() for day in dates),
        },
    )


def is_voice(event: SessionDecayEvent) -> bool:
    """Say whether this session started life as a recording."""
    return event.source_modality is SourceModality.VOICE_NOTE


__all__ = [
    "USER_ROLE",
    "AI_ROLE",
    "is_chat_buffer",
    "user_messages",
    "render_monologue",
    "render_dialogue",
    "word_count",
    "text_hash",
    "all_turns_operational",
    "has_extractable_text",
    "warn_on_multi_date",
    "is_voice",
]
