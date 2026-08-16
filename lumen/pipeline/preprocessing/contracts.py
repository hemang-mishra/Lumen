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


class AssistantDigest(BaseModel):
    """
    One of the assistant's replies, reduced to its point.

    The assistant is verbose in a way people are not — it restates, offers
    three framings, and asks a closing question. Carrying all of that into
    the entry would bury the person's own words under someone else's prose.
    Dropping it entirely is worse: half of what they say only means anything
    as an answer to what was just asked.

    So the assistant's side is kept, condensed. Only this side is condensed;
    what the person wrote is never touched.

    Attributes:
        message_id: Which reply this stands in for.
        digest: What it said, in a sentence or two.
    """

    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(min_length=1)
    digest: str = ""


class ConversationResponse(BaseModel):
    """
    What comes back from reading a conversation.

    Attributes:
        turns: One verdict per message the person wrote.
        assistant_digests: One short stand-in per assistant reply.
        session_summary: The conclusions the conversation arrived at. Kept as
            a record of where the evening landed — it is no longer what the
            rest of the stage reads, because a conversation's value is not
            only its conclusions.
        co_created_spans: The assistant's own phrasings that the person took
            up as their own, quoted exactly. Knowing which message showed
            agreement is not enough later on: by extraction time the
            assistant's side has been condensed, and there is no way left to
            tell whose words an idea started as.
    """

    model_config = ConfigDict(extra="forbid")

    turns: list[TurnClassification] = Field(default_factory=list)
    assistant_digests: list[AssistantDigest] = Field(default_factory=list)
    session_summary: str = ""
    co_created_spans: list[str] = Field(default_factory=list)


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


class SegmentedByTurns(BaseModel):
    """
    One topic of a conversation, named by the turns that make it up.

    Attributes:
        episode_summary: A one-line description of what this piece is about.
        turn_numbers: The numbered turns belonging to it. They need not be
            contiguous — a conversation returns to a subject after wandering
            off it, and both parts are the same topic.
        overarching_themes: Broad tags describing the subject matter.
        historical_era: A named period of the person's past this is anchored
            to, when they named one.
    """

    model_config = ConfigDict(extra="forbid")

    episode_summary: str = Field(min_length=1)
    turn_numbers: list[int] = Field(default_factory=list)
    overarching_themes: list[str] = Field(default_factory=list)
    historical_era: str | None = None


class DialogueStructureResponse(BaseModel):
    """
    What comes back from splitting a conversation into topics.

    Says which turns belong together rather than repeating them. Asking a
    model to echo a whole evening back, divided up, costs as much output as
    the conversation is long, runs into the reply limit, and invites it to
    paraphrase on the way past — and paraphrasing here would replace the
    person's words with a model's, which is the one thing this stage exists
    to avoid. Numbers cannot be paraphrased.

    Attributes:
        episodes: The topics, named by their turns.
        coreference: Who the conversation's pronouns and nicknames refer to.
    """

    model_config = ConfigDict(extra="forbid")

    episodes: list[SegmentedByTurns] = Field(default_factory=list)
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
    A conversation, read and rebuilt as something to work from.

    Attributes:
        entry_text: The text the rest of the stage will work from — every
            expressive thing the person wrote, word for word, with the
            assistant's replies standing between them in condensed form. This
            used to be the summary alone, and reading fifteen thousand words
            of thinking as two hundred words of conclusions is the difference
            between a history that holds how somebody got somewhere and one
            that holds only where they arrived.
        settled_summary: What the conversation concluded, kept as a record of
            the session rather than as its replacement.
        turn_acts: What each of the person's messages was doing, keyed by
            message id.
        assistant_digests: The short stand-in for each assistant reply, keyed
            by message id. Kept so the same entry can be rebuilt later in the
            stage without asking again.
        co_created_message_ids: Messages where the person took up an idea
            from the assistant.
        co_created_spans: The assistant phrasings behind those moments, in
            the assistant's own words. Empty when the reading failed, which
            means everything downstream is credited to the person alone —
            the cautious direction, since ideas marked as assistant-derived
            are trusted less when the history is searched later.
        used_fallback: True when the reading failed and the person's
            messages were simply strung together instead.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    entry_text: str
    settled_summary: str = ""
    turn_acts: dict[str, DialogueAct] = Field(default_factory=dict)
    assistant_digests: dict[str, str] = Field(default_factory=dict)
    co_created_message_ids: tuple[str, ...] = ()
    co_created_spans: tuple[str, ...] = ()
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
        unscored: The positions, counting from 1, that nobody managed to
            read. Kept apart from the scores because "we do not know" is not
            "there is nothing here". Treating the two the same is what sent
            a forty-message evening down the path meant for one-line notes
            when a scoring call happened to fail: an unread piece is given
            the close reading, since a broken call is not evidence about the
            writing.
        used_fallback: True when scoring failed outright.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    scores: tuple[EpisodeScore, ...]
    unscored: tuple[int, ...] = ()
    used_fallback: bool = False

    def was_scored(self, episode_index: int) -> bool:
        """Say whether anybody actually managed to read this piece."""
        return episode_index not in self.unscored


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
    "AssistantDigest",
    "ConversationResponse",
    "NormalizeResponse",
    "SegmentedEpisode",
    "SegmentedByTurns",
    "CoreferencePayload",
    "StructureResponse",
    "DialogueStructureResponse",
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
