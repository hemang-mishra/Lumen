"""
The shapes preprocessing hands around internally.

Two kinds of model live here. The *response* models describe what a language
model is asked to return, and are what the request is built from — the model
is shown the shape and fills it in. The *result* models are what each step
hands back to the stage, and add the one thing the language model cannot
report: whether the step actually worked, or whether its answer was
unusable and a safe stand-in was used instead.

Keeping those separate is what lets a step fail without the stage failing.
The stage reads the result, never the raw reply.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from lumen.schemas.enums import DialogueAct
from lumen.schemas.pipeline import AmbiguousRef, CoreferenceMap, ResolvedEntity

# ---------------------------------------------------------------------------
# What the language model is asked to return
# ---------------------------------------------------------------------------


class TurnClassification(BaseModel):
    """
    A verdict on one message in a conversation.

    Attributes:
        message_id: Which message this is about.
        dialogue_act: Whether the person was asking the system for something
            or saying something about themselves.
        co_created_marker: True when the person explicitly took up an idea
            the assistant had just offered — "I'm going to use that framing".
            What follows from such a moment is partly the assistant's, and
            the history should say so rather than crediting it entirely to
            the person.
    """

    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(min_length=1)
    dialogue_act: DialogueAct
    co_created_marker: bool = False


class ConversationResponse(BaseModel):
    """
    What comes back from reading a conversation.

    Attributes:
        turns: One verdict per message the person wrote.
        session_summary: The conclusions the conversation actually arrived
            at, with the exploring left out. A conversation is full of ideas
            that were tried and dropped; recording those as though they were
            settled would fill the history with things the person had
            already talked themselves out of.
    """

    model_config = ConfigDict(extra="forbid")

    turns: list[TurnClassification] = Field(default_factory=list)
    session_summary: str = ""


class NormalizeResponse(BaseModel):
    """
    What comes back from cleaning a piece of writing.

    Attributes:
        cleaned_text: The writing in plain English, with hesitations and
            abandoned false starts removed.
        detected_languages: The languages that were found in the original,
            as short codes. Recorded so a translation can be accounted for
            afterwards.
        translated: True when any part of the text had to be translated.
    """

    model_config = ConfigDict(extra="forbid")

    cleaned_text: str = ""
    detected_languages: list[str] = Field(default_factory=list)
    translated: bool = False


class SegmentedEpisode(BaseModel):
    """
    One topic pulled out of a longer entry.

    People do not write about one thing at a time, and they do not write in
    order. An entry can move from a morning argument to a thought about work
    and back again, so the pieces are split by what they are about rather
    than by where they fall in the text.

    Attributes:
        episode_summary: A one-line description of what this piece is about.
        text: The writing belonging to this topic.
        overarching_themes: Broad tags describing the subject matter.
        historical_era: A named period of the person's past that this piece
            is anchored to, when they named one.
    """

    model_config = ConfigDict(extra="forbid")

    episode_summary: str = Field(min_length=1)
    text: str = Field(min_length=1)
    overarching_themes: list[str] = Field(default_factory=list)
    historical_era: str | None = None


class CoreferencePayload(BaseModel):
    """
    Who the pronouns and nicknames in an entry refer to.

    Deliberately does not carry the entry's own id. The entry is already
    known by the time this is requested, and asking a model to repeat
    something we already have is an invitation for it to be wrong.

    Attributes:
        resolved_entities: References matched confidently to one person.
        ambiguous_refs: References that could be more than one person, kept
            unresolved rather than guessed at.
    """

    model_config = ConfigDict(extra="forbid")

    resolved_entities: list[ResolvedEntity] = Field(default_factory=list)
    ambiguous_refs: list[AmbiguousRef] = Field(default_factory=list)


class StructureResponse(BaseModel):
    """
    What comes back from splitting an entry into topics.

    Attributes:
        episodes: The topics the entry was split into, in the order they
            should be read.
        coreference: Who the entry's pronouns and nicknames refer to.
    """

    model_config = ConfigDict(extra="forbid")

    episodes: list[SegmentedEpisode] = Field(default_factory=list)
    coreference: CoreferencePayload = Field(default_factory=CoreferencePayload)


class EpisodeScore(BaseModel):
    """
    A judgement on how complete one piece of writing is.

    Attributes:
        episode_index: Which piece this is about, counting from 1.
        coherence_score: How fully formed the thought is, from 0.0 for
            something with nothing to take from it, to 1.0 for a clear
            thought with a subject, a feeling and a context.
        reason: A one-sentence explanation of the score.
        reflection_prompts: Questions that would draw more out of the person,
            asked when the piece was too thin to work with.
    """

    model_config = ConfigDict(extra="forbid")

    episode_index: int = Field(ge=1)
    coherence_score: float = Field(ge=0.0, le=1.0)
    reason: str = ""
    reflection_prompts: list[str] = Field(default_factory=list)


class TriageResponse(BaseModel):
    """Scores for every piece an entry was split into."""

    model_config = ConfigDict(extra="forbid")

    scores: list[EpisodeScore] = Field(default_factory=list)


class ReflectionPromptsResponse(BaseModel):
    """
    Questions offered back to someone whose entry was too short to work with.

    Asked on its own, rather than as part of scoring, because for a very
    short entry there is nothing to score — the entry is thin by measurement,
    and the only useful thing left to do is invite more.
    """

    model_config = ConfigDict(extra="forbid")

    reflection_prompts: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# What each step hands back to the stage
# ---------------------------------------------------------------------------


class ConversationResult(BaseModel):
    """
    A conversation, read and reduced to what was settled.

    Attributes:
        summary: The text the rest of the stage will work from.
        turn_acts: What each of the person's messages was doing, keyed by
            message id.
        co_created_message_ids: Messages where the person took up an idea
            from the assistant.
        used_fallback: True when the reading failed and the person's
            messages were simply strung together instead.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: str
    turn_acts: dict[str, DialogueAct] = Field(default_factory=dict)
    co_created_message_ids: tuple[str, ...] = ()
    used_fallback: bool = False


class NormalizeResult(BaseModel):
    """
    A piece of writing, cleaned and in English.

    Attributes:
        text: The cleaned writing.
        detected_languages: Languages found in the original.
        translated: True when part of the text was translated.
        fillers_removed: How many hesitation sounds the pattern pass took
            out before the model saw the text.
        used_fallback: True when cleaning failed and the text was kept as it
            arrived.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    detected_languages: tuple[str, ...] = ()
    translated: bool = False
    fillers_removed: int = 0
    used_fallback: bool = False


class StructureResult(BaseModel):
    """
    An entry split into topics, with its references resolved.

    Attributes:
        episodes: The topics, in reading order. Never empty — a failure
            produces one topic holding everything.
        coreference_map: Who the entry's pronouns refer to.
        used_fallback: True when splitting failed and the entry was kept
            whole.
        overflow_merged: True when the split produced more pieces than are
            allowed and the surplus was folded into the last one.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    episodes: tuple[SegmentedEpisode, ...]
    coreference_map: CoreferenceMap
    used_fallback: bool = False
    overflow_merged: bool = False


class TriageResult(BaseModel):
    """
    How much attention each piece of an entry earned.

    Attributes:
        scores: One score per piece, in the same order as the pieces.
        used_fallback: True when scoring failed. Everything is then treated
            as thin, which is the cautious direction — an unscored piece
            must never be waved through into deep analysis on the strength
            of a broken reply.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    scores: tuple[EpisodeScore, ...]
    used_fallback: bool = False


class ReflectionPromptsResult(BaseModel):
    """
    Follow-up questions for a thin entry.

    Attributes:
        prompts: The questions to offer back.
        used_fallback: True when the request failed. The list is then empty
            rather than filled with generic questions — a question that
            shows nobody read the entry is worse than no question at all.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    prompts: tuple[str, ...] = ()
    used_fallback: bool = False


def empty_coreference_map(entry_id: str) -> CoreferenceMap:
    """Build a map that resolved nothing, for when the reading failed."""
    return CoreferenceMap(entry_id=entry_id, resolved_entities=[], ambiguous_refs=[])


__all__ = [
    "TurnClassification",
    "ConversationResponse",
    "NormalizeResponse",
    "SegmentedEpisode",
    "CoreferencePayload",
    "StructureResponse",
    "EpisodeScore",
    "TriageResponse",
    "ReflectionPromptsResponse",
    "ConversationResult",
    "NormalizeResult",
    "StructureResult",
    "TriageResult",
    "ReflectionPromptsResult",
    "empty_coreference_map",
]
