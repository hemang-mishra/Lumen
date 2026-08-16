"""
Reading an exported chat conversation.

The shape this handles is one conversation per file: an object with an id, a
title, a last-updated time, and a flat list of messages, each with its own
id, role, text and timestamp. A file holding a list of those objects is
accepted too, since an export of several conversations is the obvious next
thing somebody tries.

Nothing here touches a database, a clock, or the configuration. Given the
same bytes it returns the same reading every time, which is what makes it
possible to check what a file means without standing anything up.

Two decisions are worth stating plainly, because both are choices and
neither is forced.

**One date for the whole conversation, taken from its first message.** A
reflection that starts at eleven at night and runs past midnight is one
evening's thinking, and splitting it in two at the stroke of twelve would
produce a second entry about the same evening that the graph would then have
to reconcile against the first. The day something belongs to is the day it
started.

**The reading is generous and the reporting is not.** A missing timestamp,
text arriving as a list of fragments, a message with no id — each of these
is worked around rather than refused, because an export is somebody's real
history and refusing the file loses all of it. But every accommodation is
counted and handed back, so nothing is quietly changed.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import UTC, datetime, tzinfo
from typing import Any

from lumen.ingest.contracts import (
    ImportPlan,
    ParsedConversation,
    ParsedMessage,
    RejectedConversation,
)

logger = logging.getLogger(__name__)

# What each exported role is called here. Anything absent from this mapping
# is not a person or an assistant talking — a system preamble, a tool
# result — and has no place in a record of what somebody said.
ROLE_MAP: dict[str, str] = {
    "user": "USER",
    "human": "USER",
    "assistant": "AI",
    "ai": "AI",
}

# A placeholder the exporter leaves where the assistant cited a stored
# memory. It is not a word anybody wrote, and left in place it becomes a
# noun the extraction stage has to make sense of.
#
# The leading spaces are part of the pattern because these markers usually
# sit at the end of a sentence. Taking out only the word would leave the
# space that preceded it hanging at the end of the line.
ARTEFACT_PATTERN = re.compile(r"[ \t]*\bmemcite\b", re.IGNORECASE)

# The two shapes of debris a removal leaves behind: a line that now ends in
# spaces, and a run of blank lines where a paragraph used to end.
TRAILING_SPACES = re.compile(r"[ \t]+\n")
EXCESS_BLANK_LINES = re.compile(r"\n{3,}")


class ExportFormatError(ValueError):
    """
    The file is not an export this can read at all.

    Raised only when there is nothing to salvage: the bytes are not an
    object or a list of them, or every conversation inside was unreadable.
    A file where some conversations fail returns the ones that did not,
    with the failures listed alongside them.
    """


def parse_export(
    payload: Any,
    *,
    filename: str = "",
    local_timezone: tzinfo = UTC,
) -> ImportPlan:
    """
    Read an export into the conversations it describes.

    Args:
        payload: The decoded JSON — one conversation object, or a list of
            them.
        filename: What the file was called, carried through for the history
            view. Nothing keys off it.
        local_timezone: The zone the person's days are measured in. Only
            used to work out which calendar day a conversation belongs to;
            the timestamps themselves are kept exactly as exported. This is
            a parameter rather than a configuration read because a function
            that reaches for settings cannot be reasoned about from its
            inputs alone.

    Returns:
        Every conversation that could be read, and every one that could not
        with the reason it was dropped.

    Raises:
        ExportFormatError: The payload is not a conversation or a list of
            conversations, or nothing in it could be read.
    """
    raw_conversations = _as_conversation_list(payload)

    conversations: list[ParsedConversation] = []
    rejected: list[RejectedConversation] = []

    for index, raw in enumerate(raw_conversations):
        if not isinstance(raw, dict):
            rejected.append(
                RejectedConversation(
                    reason=f"entry {index} is not a conversation object"
                )
            )
            continue
        parsed, rejection = _read_conversation(raw, index, local_timezone)
        if parsed is not None:
            conversations.append(parsed)
        if rejection is not None:
            rejected.append(rejection)

    if not conversations:
        raise ExportFormatError(
            "nothing in this file could be read as a conversation: "
            + "; ".join(item.reason for item in rejected)
        )

    logger.info(
        "read an export",
        extra={
            "source_file": filename,
            "conversations": len(conversations),
            "rejected": len(rejected),
        },
    )
    return ImportPlan(filename=filename, conversations=conversations, rejected=rejected)


def _as_conversation_list(payload: Any) -> list[Any]:
    """
    Get to the list of conversations, whatever the file wrapped them in.

    Three shapes are accepted: a single conversation, a bare list of them,
    and an object with the list under a "conversations" key. All three turn
    up in the wild, and telling them apart is a two-line job that saves
    somebody reformatting their own export by hand.
    """
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("conversations", "chats", "items"):
            nested = payload.get(key)
            if isinstance(nested, list):
                return nested
        return [payload]
    raise ExportFormatError(
        "expected a conversation object or a list of them, got "
        f"{type(payload).__name__}"
    )


def _read_conversation(
    raw: dict[str, Any], index: int, local_timezone: tzinfo
) -> tuple[ParsedConversation | None, RejectedConversation | None]:
    """
    Read one conversation, or explain why it cannot be read.

    Returns a pair so both outcomes travel the same path. A conversation
    that parses but dropped some of its messages produces neither half of a
    failure — the drops are recorded on the conversation itself, where
    whoever uploaded the file will actually see them.
    """
    title = _as_text(raw.get("title"))
    raw_messages = raw.get("messages")

    if not isinstance(raw_messages, list) or not raw_messages:
        return None, RejectedConversation(
            source_conversation_id=_as_text(raw.get("id")),
            title=title,
            reason="it has no messages",
        )

    fallback = _read_timestamp(raw.get("lastUpdated") or raw.get("last_updated"))
    messages, skipped, artefacts = _read_messages(raw_messages, fallback)

    if not messages:
        return None, RejectedConversation(
            source_conversation_id=_as_text(raw.get("id")),
            title=title,
            reason="none of its messages could be read",
        )

    if not any(message.role == "USER" for message in messages):
        return None, RejectedConversation(
            source_conversation_id=_as_text(raw.get("id")),
            title=title,
            reason="it contains nothing the person wrote themselves",
        )

    return (
        ParsedConversation(
            source_conversation_id=_as_text(raw.get("id")) or _derived_id(messages, index),
            title=title,
            # The whole conversation is filed under the day it began. See the
            # module docstring for why midnight does not split it.
            event_date=messages[0].timestamp.astimezone(local_timezone).date(),
            messages=messages,
            skipped_roles=skipped,
            artefacts_removed=artefacts,
        ),
        None,
    )


def _read_messages(
    raw_messages: list[Any], fallback: datetime | None
) -> tuple[list[ParsedMessage], dict[str, int], int]:
    """
    Read every message that can be read, in order.

    A message with no timestamp of its own inherits the one before it, and
    the first message falls back to the conversation's last-updated time.
    That ordering matters: inheriting forwards keeps a conversation's
    messages in sequence, where defaulting everything to one time would
    collapse an evening of thinking into a single instant.
    """
    messages: list[ParsedMessage] = []
    skipped: dict[str, int] = {}
    artefacts = 0
    last_seen = fallback

    for index, raw in enumerate(raw_messages):
        if not isinstance(raw, dict):
            skipped["unreadable"] = skipped.get("unreadable", 0) + 1
            continue

        raw_role = _as_text(raw.get("role")).lower()
        role = ROLE_MAP.get(raw_role)
        if role is None:
            key = raw_role or "unknown"
            skipped[key] = skipped.get(key, 0) + 1
            continue

        content, removed = _clean(_read_content(raw.get("content")))
        if not content:
            skipped["empty"] = skipped.get("empty", 0) + 1
            continue

        timestamp = _read_timestamp(raw.get("timestamp") or raw.get("create_time"))
        timestamp = timestamp or last_seen
        if timestamp is None:
            skipped["undated"] = skipped.get("undated", 0) + 1
            continue
        last_seen = timestamp

        artefacts += removed
        messages.append(
            ParsedMessage(
                message_id=_as_text(raw.get("id")) or f"msg-{index}",
                role=role,
                content=content,
                timestamp=timestamp,
            )
        )

    return messages, skipped, artefacts


def _read_content(value: Any) -> str:
    """
    Get the text out, whether it arrived as a string or in pieces.

    Some exporters write the message as a list of fragments, and some of
    those fragments are objects with the text under a key rather than bare
    strings. Both are joined back into the paragraph the person actually
    wrote.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = [_read_content(part) for part in value]
        return "\n\n".join(part for part in parts if part.strip())
    if isinstance(value, dict):
        for key in ("text", "content", "parts"):
            if key in value:
                return _read_content(value[key])
    return ""


def _clean(text: str) -> tuple[str, int]:
    """
    Take the exporter's own markers out of the text.

    Returns the cleaned text and how many markers were removed, because a
    change nobody can count is a change nobody can check.
    """
    cleaned, removed = ARTEFACT_PATTERN.subn("", text)
    if removed:
        cleaned = TRAILING_SPACES.sub("\n", cleaned)
        cleaned = EXCESS_BLANK_LINES.sub("\n\n", cleaned)
    return cleaned.strip(), removed


def _read_timestamp(value: Any) -> datetime | None:
    """
    Read a time, from any of the three ways exports write one.

    ISO-8601 text with a trailing Z, ISO-8601 text with an offset, and a
    plain number of seconds since the epoch. Anything without a zone is
    read as UTC rather than as local time, so the same file read on two
    machines gives the same answer.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(value, tz=UTC)
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip())
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _derived_id(messages: list[ParsedMessage], index: int) -> str:
    """
    An identifier for a conversation the export did not name.

    Built from the message ids rather than from the position in the file,
    so that uploading the same export twice is still recognised as the same
    conversation. The position is only mixed in to keep two genuinely
    identical conversations in one file apart.
    """
    digest = hashlib.sha256(
        "|".join(message.message_id for message in messages).encode("utf-8")
    ).hexdigest()[:16]
    return f"conv-{index}-{digest}"


def _as_text(value: Any) -> str:
    """A string field, with anything that is not a string treated as absent."""
    return value.strip() if isinstance(value, str) else ""


__all__ = ["parse_export", "ExportFormatError", "ROLE_MAP"]
