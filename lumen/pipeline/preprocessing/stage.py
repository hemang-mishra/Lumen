"""
The first stage of the pipeline: getting raw input ready to be understood.

What arrives here is whatever a person actually produced — a voice note full
of hesitation, a conversation half of which the assistant wrote, an entry
that switches language mid-sentence. What leaves is clean English, split by
subject, with a decision about how much attention each piece has earned.

The most important thing this stage does is refuse. A twenty-word mumble
handed to an extraction model does not produce nothing; it produces
something confident and invented. Everything downstream trusts that what
reaches it is worth reading closely, and this is the only place that
promise is kept.

Nothing here touches a database. The language models it needs are handed to
it, so the whole stage can be run with nothing installed and its behaviour
checked exactly.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from lumen.config import AppConfig, PipelineConfig
from lumen.pipeline.preprocessing import passes, transcript
from lumen.pipeline.preprocessing.contracts import (
    ConversationResult,
    EpisodeScore,
    NormalizeResult,
    SegmentedEpisode,
    StructureResult,
    TriageResult,
    empty_coreference_map,
)
from lumen.providers.protocols import LLMProvider
from lumen.schemas.enums import EntryClass, QualityGateDecision
from lumen.schemas.ids import make_node_id
from lumen.schemas.pipeline import (
    CoreferenceMap,
    PreprocessedEpisode,
    PreprocessingResult,
    SessionDecayEvent,
)

logger = logging.getLogger(__name__)

# Prefix used when naming episodes, matching the graph's episode nodes.
_EPISODE_ID_PREFIX = "ep"


@dataclass
class _Trail:
    """
    A record of which steps ran and how they went.

    Collected as the stage works through a session so that the closing log
    line can say what actually happened — how much was translated, which
    steps had to fall back — without every function having to thread those
    details through its return value.
    """

    conversation: ConversationResult | None = None
    normalized: NormalizeResult | None = None
    structured: StructureResult | None = None
    triaged: TriageResult | None = None

    def fallbacks(self) -> list[str]:
        """Name the steps that had to use their stand-in answer."""
        outcomes = {
            "conversation": self.conversation,
            "normalize": self.normalized,
            "structure": self.structured,
            "triage": self.triaged,
        }
        return [
            name
            for name, outcome in outcomes.items()
            if outcome is not None and outcome.used_fallback
        ]


@dataclass
class _Outcome:
    """Everything needed to build the stage's result and log line."""

    episodes: list[PreprocessedEpisode]
    coreference_map: CoreferenceMap
    decision: QualityGateDecision
    trail: _Trail
    reason: str | None = None
    word_count: int = 0
    pending_reflections: list[str] = field(default_factory=list)


def preprocess(
    event: SessionDecayEvent,
    *,
    lightweight: LLMProvider,
    thinking: LLMProvider,
    config: AppConfig | None = None,
) -> PreprocessingResult:
    """
    Turn one finished session into clean, sorted episodes.

    Two models are asked for rather than one, because the work splits into
    two kinds. Cleaning text and scoring it are quick, repetitive jobs.
    Untangling a conversation and deciding where one topic ends and another
    begins need actual reasoning. Naming both in the signature keeps that
    split visible instead of buried.

    The order of the work matters and is not the obvious one:

      1. Untangle the conversation, if this was one.
      2. Clean and translate.
      3. Give up early if nothing is left, or if there is too little to
         work with.
      4. Split into topics.
      5. Score each topic.

    Scoring comes last because topics are what get scored, and they do not
    exist until step 4. The cheap rejection still happens first — step 3
    does it by counting words, so a short entry never costs a reasoning
    call.
    """
    started = time.perf_counter()
    settings = (config or AppConfig()).pipeline
    trail = _Trail()

    transcript.warn_on_multi_date(event)

    trail.conversation = _read_conversation(event, provider=thinking)
    trail.normalized = passes.run_normalize(
        trail.conversation.summary,
        is_voice=transcript.is_voice(event),
        provider=lightweight,
    )
    text = trail.normalized.text

    if _should_discard(text, trail.conversation):
        return _finish(event, started, _discarded(event, trail))

    words = transcript.word_count(text)
    if words < settings.min_reflection_words:
        outcome = _too_short(
            event, text=text, trail=trail, provider=lightweight, config=settings
        )
        return _finish(event, started, outcome)

    trail.structured = passes.run_structure(
        text, entry_id=event.session_id, provider=thinking, config=settings
    )
    trail.triaged = passes.run_triage(
        [episode.text for episode in trail.structured.episodes],
        provider=lightweight,
        config=settings,
    )

    episodes = _build_episodes(
        event, trail.structured.episodes, trail.triaged.scores, settings
    )
    return _finish(
        event,
        started,
        _Outcome(
            episodes=episodes,
            coreference_map=trail.structured.coreference_map,
            decision=_decide(episodes),
            trail=trail,
            word_count=words,
            pending_reflections=_collect_prompts(episodes, trail.triaged),
        ),
    )


# ---------------------------------------------------------------------------
# The individual decisions
# ---------------------------------------------------------------------------


def _read_conversation(
    event: SessionDecayEvent, *, provider: LLMProvider
) -> ConversationResult:
    """
    Reduce a conversation to its conclusions, or pass a monologue straight
    through.

    Whether this was a conversation is decided by whether the assistant
    spoke in it, so a voice note or a pasted entry skips this entirely and
    costs nothing.
    """
    if transcript.is_chat_buffer(event):
        return passes.run_conversation(event.raw_buffer, provider=provider)
    spoken = transcript.user_messages(event.raw_buffer)
    return ConversationResult(summary=transcript.render_monologue(spoken))


def _should_discard(text: str, conversation: ConversationResult) -> bool:
    """
    Decide whether there is anything here at all.

    This is the one decision in the stage that throws someone's input away,
    so no model gets a vote in it. Both tests are things that can be
    counted: is there any text left after cleaning, and was every message
    the person wrote a request for information rather than something about
    themselves.

    A rambling, barely coherent entry is not discarded. That is what the
    lighter handling further down exists for.
    """
    if not transcript.has_extractable_text(text):
        return True
    return transcript.all_turns_operational(conversation.turn_acts)


def _classify(coherence: float, config: PipelineConfig) -> EntryClass:
    """Decide whether one topic earned a close reading or a light one."""
    if coherence >= config.coherence_threshold:
        return EntryClass.REFLECTION
    return EntryClass.RAW_CAPTURE


def _decide(episodes: list[PreprocessedEpisode]) -> QualityGateDecision:
    """
    Settle on one verdict for the whole session.

    A session counts as worth reading closely if any single part of it was.
    One real reflection buried among small talk is still a real reflection,
    and averaging it away would lose exactly the entries worth keeping.
    """
    if any(episode.entry_class == EntryClass.REFLECTION for episode in episodes):
        return QualityGateDecision.REFLECTION
    return QualityGateDecision.RAW_CAPTURE


# ---------------------------------------------------------------------------
# Building the pieces
# ---------------------------------------------------------------------------


def _build_episodes(
    event: SessionDecayEvent,
    segments: tuple[SegmentedEpisode, ...],
    scores: tuple[EpisodeScore, ...],
    config: PipelineConfig,
) -> list[PreprocessedEpisode]:
    """
    Pair each topic with its score and give it a name.

    Each topic is judged on its own, so an entry holding one careful
    reflection and one throwaway aside produces two pieces treated
    differently, rather than one verdict stretched over both.
    """
    total = len(segments)
    built = []
    for position, segment in enumerate(segments, start=1):
        coherence = scores[position - 1].coherence_score if position <= len(scores) else 0.0
        built.append(
            PreprocessedEpisode(
                episode_id=make_node_id(_EPISODE_ID_PREFIX, event.event_date, position),
                episode_summary=segment.episode_summary,
                episode_index=position,
                total_episodes_in_entry=total,
                cleaned_text=segment.text,
                entry_class=_classify(coherence, config),
                coherence_score=coherence,
                historical_era=segment.historical_era,
                overarching_themes=segment.overarching_themes,
                raw_text_hash=transcript.text_hash(segment.text),
            )
        )
    return built


def _discarded(event: SessionDecayEvent, trail: _Trail) -> _Outcome:
    """Build the result for a session with nothing extractable in it."""
    return _Outcome(
        episodes=[],
        coreference_map=empty_coreference_map(event.session_id),
        decision=QualityGateDecision.DISCARD,
        trail=trail,
        reason="nothing extractable remained after cleaning",
    )


def _too_short(
    event: SessionDecayEvent,
    *,
    text: str,
    trail: _Trail,
    provider: LLMProvider,
    config: PipelineConfig,
) -> _Outcome:
    """
    Handle an entry too short to be worth taking apart.

    Nothing is split and nothing is scored — there is not enough here for
    either to mean anything, and paying a reasoning model to confirm that a
    fifteen-word entry is short would be silly. It is kept whole, marked as
    a light capture, and the person is asked a few questions that might draw
    more out of them.

    The score is recorded as 0.0, which means "never established" rather
    than "judged worthless". The classification is the field to trust here.
    """
    prompts = passes.run_reflection_prompts(text, provider=provider, config=config)
    episode = PreprocessedEpisode(
        episode_id=make_node_id(_EPISODE_ID_PREFIX, event.event_date, 1),
        episode_summary=passes.local_summary(text),
        episode_index=1,
        total_episodes_in_entry=1,
        cleaned_text=text,
        entry_class=EntryClass.RAW_CAPTURE,
        coherence_score=0.0,
        raw_text_hash=transcript.text_hash(text),
    )
    return _Outcome(
        episodes=[episode],
        coreference_map=empty_coreference_map(event.session_id),
        decision=QualityGateDecision.RAW_CAPTURE,
        trail=trail,
        reason="too short to segment",
        word_count=transcript.word_count(text),
        pending_reflections=list(prompts.prompts),
    )


def _collect_prompts(
    episodes: list[PreprocessedEpisode], triaged: TriageResult
) -> list[str]:
    """
    Gather the follow-up questions raised for the thin parts of an entry.

    Only questions attached to a piece that was actually marked as thin are
    kept, and repeats are dropped while keeping the order they arrived in.
    """
    light = {
        episode.episode_index
        for episode in episodes
        if episode.entry_class == EntryClass.RAW_CAPTURE
    }
    seen: set[str] = set()
    collected = []
    for score in triaged.scores:
        if score.episode_index not in light:
            continue
        for prompt in score.reflection_prompts:
            if prompt not in seen:
                seen.add(prompt)
                collected.append(prompt)
    return collected


def _finish(
    event: SessionDecayEvent, started: float, outcome: _Outcome
) -> PreprocessingResult:
    """
    Assemble the result and record one line about how it went.

    The log line carries counts and outcomes but never any of the writing
    itself, so the log does not slowly become a second copy of somebody's
    private history.
    """
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    normalized = outcome.trail.normalized
    conversation = outcome.trail.conversation
    adopted = list(conversation.co_created_spans) if conversation else []

    logger.info(
        "preprocessing complete",
        extra={
            "session_id": event.session_id,
            "source_modality": event.source_modality.value,
            "was_conversation": transcript.is_chat_buffer(event),
            "messages_in": event.message_count,
            "clean_word_count": outcome.word_count,
            "episode_count": len(outcome.episodes),
            "decision": outcome.decision.value,
            "reason": outcome.reason,
            "detected_languages": list(normalized.detected_languages) if normalized else [],
            "translated": bool(normalized and normalized.translated),
            "fillers_removed": normalized.fillers_removed if normalized else 0,
            "fallbacks": outcome.trail.fallbacks(),
            "adopted_span_count": len(adopted),
            "duration_ms": elapsed_ms,
        },
    )

    return PreprocessingResult(
        session_id=event.session_id,
        episodes=outcome.episodes,
        coreference_map=outcome.coreference_map,
        quality_gate_decision=outcome.decision,
        processing_time_ms=elapsed_ms,
        pending_reflections=outcome.pending_reflections,
        co_created_spans=adopted,
    )


__all__ = ["preprocess"]
