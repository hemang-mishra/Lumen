"""
What an uploaded file was understood to contain.

These sit between reading a file and writing anything down. Everything here
describes a *reading* of an export — nothing in this module has been saved,
and none of it names a database.

The one shape worth explaining is why rejections are carried alongside
conversations rather than raised. An export holding thirty conversations
where two are unreadable should still import twenty-eight, and the person
uploading it should be told exactly which two were dropped and why. An
exception can only say "this file was bad", which is both less true and
less useful.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class ParsedMessage(BaseModel):
    """
    One message, read from an export and mapped onto Lumen's vocabulary.

    Attributes:
        message_id: The export's own identifier for this message, kept so a
            re-import lands on the same message rather than a duplicate of
            it. Derived from its position when the export has none.
        role: "USER" or "AI", the only two roles a session buffer knows.
            Anything else in the file — a system note, a tool result — is
            dropped before a message ever becomes one of these.
        content: The text, after export artefacts have been taken out.
        timestamp: When it was sent, always with a timezone attached.
    """

    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(min_length=1)
    role: str = Field(pattern="^(USER|AI)$")
    content: str = Field(min_length=1)
    timestamp: datetime


class ParsedConversation(BaseModel):
    """
    One conversation, ready to be staged.

    Attributes:
        source_conversation_id: The export's identifier for this
            conversation. This is the dedupe key — uploading the same file
            twice is recognised by this value, not by the file's name.
        title: What the export called it. Becomes the label that
            distinguishes two conversations held on the same day.
        event_date: The day this conversation belongs to. Taken from the
            first surviving message and applied to the whole conversation,
            including the part of it that ran past midnight.
        messages: Every usable message, in the order they were sent.
        skipped_roles: How many messages were dropped, counted by the role
            they carried. Reported rather than silently discarded, because
            a file that turns out to be nine tenths tool output should say
            so before anybody wonders where their history went.
        artefacts_removed: How many export markers were taken out of the
            text. Same reasoning: a number nobody reads is still better
            than a change nobody can see.
    """

    model_config = ConfigDict(extra="forbid")

    source_conversation_id: str = Field(min_length=1)
    title: str = ""
    event_date: date
    messages: list[ParsedMessage] = Field(min_length=1)
    skipped_roles: dict[str, int] = Field(default_factory=dict)
    artefacts_removed: int = Field(default=0, ge=0)

    @property
    def user_message_count(self) -> int:
        """How many of the messages the person wrote themselves."""
        return sum(1 for message in self.messages if message.role == "USER")


class RejectedConversation(BaseModel):
    """
    A conversation in the file that could not be read, and why.

    Attributes:
        source_conversation_id: Its identifier, if it had one.
        title: Its title, if it had one.
        reason: Plain language, meant to be shown to whoever uploaded the
            file. "no messages anyone could read" is an answer; a stack
            trace is not.
    """

    model_config = ConfigDict(extra="forbid")

    source_conversation_id: str = ""
    title: str = ""
    reason: str = Field(min_length=1)


class ImportPlan(BaseModel):
    """
    Everything one uploaded file was understood to contain.

    Attributes:
        filename: What the file was called when it arrived. Carried for the
            history view only; nothing keys off it.
        conversations: The ones that can be staged.
        rejected: The ones that cannot, each with its reason.
    """

    model_config = ConfigDict(extra="forbid")

    filename: str = ""
    conversations: list[ParsedConversation] = Field(default_factory=list)
    rejected: list[RejectedConversation] = Field(default_factory=list)

    @property
    def message_count(self) -> int:
        """Every usable message across every readable conversation."""
        return sum(len(conversation.messages) for conversation in self.conversations)


class StagedConversation(BaseModel):
    """
    A conversation that has been written into the waiting room and is now
    queued to be processed.

    This is the hand-off from staging to the worker, and the reason the
    upload response can name a trace before any work has been done: the
    identifiers are settled the moment the messages are stored, so a caller
    can start following a run that has not started yet.

    Attributes:
        import_id: The history row recording this conversation's import.
        session_id: The buffer its messages were written into.
        source_conversation_id: The export's identifier, carried through.
        title: The conversation's title, carried through.
        event_date: The day it was filed under.
        message_count: How many messages were stored.
        already_imported: True when this conversation was recognised from an
            earlier upload and nothing was queued. The row is still returned
            so the caller can point at the original run instead of being
            told nothing happened.
    """

    model_config = ConfigDict(extra="forbid")

    import_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    source_conversation_id: str = Field(min_length=1)
    title: str = ""
    event_date: date
    message_count: int = Field(ge=0)
    already_imported: bool = False


__all__ = [
    "ParsedMessage",
    "ParsedConversation",
    "RejectedConversation",
    "ImportPlan",
    "StagedConversation",
]
