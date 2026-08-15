"""
The steps of preprocessing that ask a language model something.

Every step here follows the same five beats: fill in a prompt, ask the
model, check something came back, check it has the right shape, and fall
back to a safe answer if any of that failed. That sequence is written once,
in _request, and each step supplies only what makes it different.

The fallbacks are the important part. A model call can fail for reasons
nobody controls — a timeout, a refusal, a reply that is not quite valid —
and none of those are reasons to lose somebody's journal entry. So every
step has a stand-in answer, and each one is chosen to fail in the cautious
direction: keep the text rather than drop it, and treat a piece of writing
as thin rather than as worth deep analysis. Losing quality is recoverable
by running again. Losing the entry, or writing conclusions drawn from a
broken reply into someone's permanent history, is not.

Every fallback is logged, because a step that quietly fails every time would
otherwise look exactly like a step that works.
"""

from __future__ import annotations

import logging
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from lumen.config import PipelineConfig
from lumen.pipeline.preprocessing.contracts import (
    ConversationResponse,
    ConversationResult,
    EpisodeScore,
    NormalizeResponse,
    NormalizeResult,
    ReflectionPromptsResponse,
    ReflectionPromptsResult,
    SegmentedEpisode,
    StructureResponse,
    StructureResult,
    TriageResponse,
    TriageResult,
    empty_coreference_map,
)
from lumen.pipeline.preprocessing.fillers import strip_standalone_fillers
from lumen.pipeline.preprocessing.prompts import (
    CONVERSATION_PROMPT,
    NORMALIZE_TEXT_PROMPT,
    NORMALIZE_VOICE_PROMPT,
    REFLECTION_PROMPTS_PROMPT,
    STRUCTURE_PROMPT,
    SYSTEM_INSTRUCTION,
    TRIAGE_PROMPT,
    render_episodes_for_triage,
)
from lumen.pipeline.preprocessing.transcript import (
    all_turns_operational,
    render_dialogue,
    render_monologue,
    user_messages,
)
from lumen.providers.errors import ProviderError
from lumen.providers.protocols import LLMProvider
from lumen.schemas.pipeline import BufferMessage, CoreferenceMap

logger = logging.getLogger(__name__)

_ResponseT = TypeVar("_ResponseT", bound=BaseModel)

# How long a stand-in summary may be when one has to be written without a
# model. Long enough to identify the entry, short enough to stay a summary.
_LOCAL_SUMMARY_CHARS = 80


def _request(
    *,
    provider: LLMProvider,
    prompt: str,
    response_model: type[_ResponseT],
    pass_name: str,
) -> _ResponseT | None:
    """
    Ask the model one question and insist on a usable answer.

    Returns None instead of raising when anything goes wrong, because every
    caller has something better to do with a failure than give up. Three
    things can go wrong and all three end the same way: the call itself
    failed, the reply was not valid JSON, or the JSON did not match the
    shape that was asked for.

    Nothing about the entry's contents is ever logged here — only which step
    failed and why.
    """
    try:
        result = provider.generate_structured(
            prompt,
            response_model,
            system_instruction=SYSTEM_INSTRUCTION,
        )
    except ProviderError as exc:
        _log_fallback(pass_name, "provider_error", type(exc).__name__)
        return None

    if result.data is None:
        _log_fallback(pass_name, "unparseable_response", result.parse_error)
        return None

    try:
        return response_model.model_validate(result.data)
    except ValidationError as exc:
        _log_fallback(pass_name, "unexpected_shape", f"{exc.error_count()} field errors")
        return None


def _log_fallback(pass_name: str, reason: str, detail: str | None = None) -> None:
    """Record that a step fell back, without recording what it was reading."""
    logger.warning(
        "preprocessing step fell back to its safe answer",
        extra={"preprocessing_pass": pass_name, "reason": reason, "detail": detail},
    )


def local_summary(text: str) -> str:
    """
    Write a one-line label for a piece of text without asking a model.

    Used when the step that would normally write a proper summary was
    skipped or failed. Takes the opening of the text rather than inventing a
    description, so the label is always something the person actually wrote.
    """
    flattened = " ".join(text.split())
    if not flattened:
        return "Empty entry"
    if len(flattened) <= _LOCAL_SUMMARY_CHARS:
        return flattened
    return flattened[:_LOCAL_SUMMARY_CHARS].rstrip() + "…"


def whole_text_episode(text: str) -> SegmentedEpisode:
    """Wrap an entire entry up as a single unsplit episode."""
    return SegmentedEpisode(episode_summary=local_summary(text), text=text)


# ---------------------------------------------------------------------------
# Step 1 — reading a conversation (conversations only)
# ---------------------------------------------------------------------------


def run_conversation(
    messages: list[BufferMessage], *, provider: LLMProvider
) -> ConversationResult:
    """
    Reduce a back-and-forth conversation to what it settled on.

    Talking something through is not the same as concluding it. A
    conversation contains ideas raised and dropped, questions, and the
    assistant's suggestions — recording all of that as though the person
    believed it would fill their history with things they had already
    talked themselves out of.

    If this fails, the person's own messages are strung together unchanged.
    That loses the tidying, but it keeps every word they wrote, which is the
    part that cannot be recovered later.
    """
    spoken_by_person = user_messages(messages)
    response = _request(
        provider=provider,
        prompt=CONVERSATION_PROMPT.format(dialogue=render_dialogue(messages)),
        response_model=ConversationResponse,
        pass_name="conversation",
    )
    if response is None:
        return ConversationResult(
            summary=render_monologue(spoken_by_person), used_fallback=True
        )

    turn_acts = {turn.message_id: turn.dialogue_act for turn in response.turns}
    co_created = tuple(turn.message_id for turn in response.turns if turn.co_created_marker)
    spans = tuple(span.strip() for span in response.co_created_spans if span.strip())
    summary = response.session_summary.strip()

    # An empty summary is the right answer when every message was a request
    # for information. It is a lost conversation when it was not, so the
    # person's words are kept instead.
    if not summary and not all_turns_operational(turn_acts):
        _log_fallback("conversation", "summary_lost_expressive_turns")
        return ConversationResult(
            summary=render_monologue(spoken_by_person),
            turn_acts=turn_acts,
            co_created_message_ids=co_created,
            co_created_spans=spans,
            used_fallback=True,
        )

    return ConversationResult(
        summary=summary,
        turn_acts=turn_acts,
        co_created_message_ids=co_created,
        co_created_spans=spans,
    )


# ---------------------------------------------------------------------------
# Step 2 — cleaning and translating
# ---------------------------------------------------------------------------


def run_normalize(
    text: str, *, is_voice: bool, provider: LLMProvider
) -> NormalizeResult:
    """
    Turn raw input into clean English.

    Runs in two halves. First a pattern pass takes out hesitation sounds,
    which is exact and free but only safe for words that can never mean
    anything. Then the model handles what needs understanding: fillers that
    are sometimes real words, spoken false starts, and translation.

    Speech and typing get different instructions. In a recording, "um" is
    noise and a false start was never meant. In typed text every word was
    chosen, so only translation applies — undoing a "correction" someone
    typed deliberately would be rewriting them.

    This runs on every entry, including entries that look like plain
    English, because the most common mixed-language writing is another
    language spelled out in ordinary letters and no cheap check catches it.

    If this fails, the pattern-cleaned text is kept as it is. Noisy text
    extracts badly, but it extracts.
    """
    pre_cleaned, fillers_removed = (
        strip_standalone_fillers(text) if is_voice else (text, 0)
    )
    template = NORMALIZE_VOICE_PROMPT if is_voice else NORMALIZE_TEXT_PROMPT

    response = _request(
        provider=provider,
        prompt=template.format(text=pre_cleaned),
        response_model=NormalizeResponse,
        pass_name="normalize",
    )
    if response is None:
        return NormalizeResult(
            text=pre_cleaned, fillers_removed=fillers_removed, used_fallback=True
        )

    cleaned = response.cleaned_text.strip()
    if not cleaned and pre_cleaned.strip():
        _log_fallback("normalize", "cleaning_returned_nothing")
        return NormalizeResult(
            text=pre_cleaned, fillers_removed=fillers_removed, used_fallback=True
        )

    return NormalizeResult(
        text=cleaned,
        detected_languages=tuple(response.detected_languages),
        translated=response.translated,
        fillers_removed=fillers_removed,
    )


# ---------------------------------------------------------------------------
# Step 3 — splitting into topics and resolving references
# ---------------------------------------------------------------------------


def run_structure(
    text: str,
    *,
    entry_id: str,
    provider: LLMProvider,
    config: PipelineConfig,
) -> StructureResult:
    """
    Split an entry by subject and work out who its pronouns refer to.

    Both jobs are done in one request because both need the whole entry.
    Working out that "he" means Jordan depends on having read where Jordan
    was introduced, which may be in a different topic entirely.

    If this fails, the entry is kept whole as a single topic with no
    references resolved. Merging two topics into one still leaves something
    that can be searched and reasoned about later. Splitting an entry in the
    wrong places, or dropping part of it, cannot be undone.
    """
    response = _request(
        provider=provider,
        prompt=STRUCTURE_PROMPT.format(text=text),
        response_model=StructureResponse,
        pass_name="structure",
    )
    if response is None or not response.episodes:
        if response is not None:
            _log_fallback("structure", "no_episodes_returned")
        return StructureResult(
            episodes=(whole_text_episode(text),),
            coreference_map=empty_coreference_map(entry_id),
            used_fallback=True,
        )

    episodes, overflow_merged = _cap_episodes(
        tuple(response.episodes), config.max_episodes_per_session
    )
    coreference_map = CoreferenceMap(
        entry_id=entry_id,
        resolved_entities=response.coreference.resolved_entities,
        ambiguous_refs=response.coreference.ambiguous_refs,
    )
    return StructureResult(
        episodes=episodes,
        coreference_map=coreference_map,
        overflow_merged=overflow_merged,
    )


def _cap_episodes(
    episodes: tuple[SegmentedEpisode, ...], cap: int
) -> tuple[tuple[SegmentedEpisode, ...], bool]:
    """
    Hold the number of topics to the allowed maximum.

    A split that shatters one evening into forty fragments has stopped
    finding topics and started cutting sentences. Rather than reject the
    whole thing, the surplus is folded into the last topic — every word is
    kept, and the ceiling holds.
    """
    if cap < 1 or len(episodes) <= cap:
        return episodes, False

    kept = episodes[: cap - 1]
    merged = episodes[cap - 1 :]
    tail = SegmentedEpisode(
        episode_summary=merged[0].episode_summary,
        text="\n\n".join(episode.text for episode in merged),
        overarching_themes=sorted(
            {theme for episode in merged for theme in episode.overarching_themes}
        ),
        historical_era=next(
            (episode.historical_era for episode in merged if episode.historical_era),
            None,
        ),
    )
    logger.warning(
        "entry split into more topics than allowed; surplus merged into the last one",
        extra={"episodes_returned": len(episodes), "episode_cap": cap},
    )
    return (*kept, tail), True


# ---------------------------------------------------------------------------
# Step 4 — scoring, and asking for more where it is thin
# ---------------------------------------------------------------------------


def run_triage(
    texts: list[str],
    *,
    provider: LLMProvider,
    config: PipelineConfig,
) -> TriageResult:
    """
    Score each topic for how complete a reflection it is.

    Scoring and question-writing share one request. The model has just
    decided a piece of writing is thin, and asking what would thicken it is
    free at that moment.

    Scores come back tagged with the topic they belong to and are matched up
    by position. A topic the model forgot to score is treated as unscored
    rather than inheriting its neighbour's verdict.

    If the whole request fails, everything is treated as thin. That is the
    cautious direction: a piece of writing nobody managed to read must not
    be waved through into deep analysis on the strength of a broken reply.
    """
    if not texts:
        return TriageResult(scores=())

    response = _request(
        provider=provider,
        prompt=TRIAGE_PROMPT.format(
            threshold=config.coherence_threshold,
            count=config.reflection_prompt_count,
            episodes=render_episodes_for_triage(texts),
        ),
        response_model=TriageResponse,
        pass_name="triage",
    )
    if response is None:
        return TriageResult(scores=_unscored(len(texts)), used_fallback=True)

    by_index = {score.episode_index: score for score in response.scores}
    matched = [index for index in range(1, len(texts) + 1) if index in by_index]
    if not matched:
        _log_fallback("triage", "no_scores_matched_episodes")
        return TriageResult(scores=_unscored(len(texts)), used_fallback=True)

    if len(matched) < len(texts):
        logger.warning(
            "some topics came back without a score and are treated as unscored",
            extra={"episodes": len(texts), "scored": len(matched)},
        )

    scores = tuple(
        by_index.get(index, _unscored_at(index)) for index in range(1, len(texts) + 1)
    )
    return TriageResult(scores=_trim_prompts(scores, config.reflection_prompt_count))


def _unscored(count: int) -> tuple[EpisodeScore, ...]:
    """Build a full set of unscored verdicts, one per topic."""
    return tuple(_unscored_at(index) for index in range(1, count + 1))


def _unscored_at(index: int) -> EpisodeScore:
    """A verdict meaning nobody managed to read this one."""
    return EpisodeScore(
        episode_index=index,
        coherence_score=0.0,
        reason="Not scored — the scoring step did not return a verdict for this topic.",
    )


def _trim_prompts(
    scores: tuple[EpisodeScore, ...], limit: int
) -> tuple[EpisodeScore, ...]:
    """Hold each topic to the agreed number of follow-up questions."""
    trimmed = []
    for score in scores:
        prompts = [prompt.strip() for prompt in score.reflection_prompts if prompt.strip()]
        trimmed.append(score.model_copy(update={"reflection_prompts": prompts[:limit]}))
    return tuple(trimmed)


def run_reflection_prompts(
    text: str,
    *,
    provider: LLMProvider,
    config: PipelineConfig,
) -> ReflectionPromptsResult:
    """
    Ask for follow-up questions on an entry too short to score.

    A very short entry does not need scoring — its thinness was settled by
    counting words, and no model opinion would change that. The only useful
    thing left is to invite the person to say more.

    If this fails, no questions are offered. Generic questions would be
    worse than silence: a question that could have been asked about any
    entry tells the person nobody read theirs.
    """
    response = _request(
        provider=provider,
        prompt=REFLECTION_PROMPTS_PROMPT.format(
            count=config.reflection_prompt_count, text=text
        ),
        response_model=ReflectionPromptsResponse,
        pass_name="reflection_prompts",
    )
    if response is None:
        return ReflectionPromptsResult(used_fallback=True)

    prompts = tuple(
        prompt.strip() for prompt in response.reflection_prompts if prompt.strip()
    )
    return ReflectionPromptsResult(prompts=prompts[: config.reflection_prompt_count])


__all__ = [
    "local_summary",
    "whole_text_episode",
    "run_conversation",
    "run_normalize",
    "run_structure",
    "run_triage",
    "run_reflection_prompts",
]
