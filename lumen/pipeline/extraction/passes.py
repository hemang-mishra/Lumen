"""
The two ways of reading an episode.

Which one runs is decided by how much the episode earned in the previous
stage. A full reflection is read closely by a model that reasons, against
the whole vocabulary of things worth noticing. A thin entry is read by a
fast model that is allowed to take almost nothing from it.

Both follow the same five beats: build the prompt, ask the model, check
something came back, check it against every rule, and turn what survived
into nodes. The parts that differ — which prompt, which model, which
rules — are the only things each function actually spells out.

If the model call fails, nothing is extracted. That is the deliberate
choice: the person's writing is already safely stored on the episode
itself, so a failure here costs an analysis that can be run again, while
inventing something to fill the gap would cost the truth of their history
permanently and undetectably.
"""

from __future__ import annotations

import logging
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from lumen.config import PipelineConfig
from lumen.pipeline.extraction.assembly import NodeFactory
from lumen.pipeline.extraction.catalog import render_type_dictionary
from lumen.pipeline.extraction.contracts import (
    ExtractionOutcome,
    RawCaptureResponse,
    ReflectionExtractionResponse,
)
from lumen.pipeline.extraction.prompts import (
    RAW_CAPTURE_PROMPT,
    REFLECTION_PROMPT,
    SYSTEM_INSTRUCTION,
    render_high_signal_types,
    render_people,
)
from lumen.pipeline.extraction.validation import (
    ValidationContext,
    ValidationReport,
    build_context,
    validate_raw_capture,
    validate_reflection,
)
from lumen.providers.errors import ProviderError
from lumen.providers.protocols import LLMProvider
from lumen.schemas.pipeline import MicroextractionInput

logger = logging.getLogger(__name__)

_ResponseT = TypeVar("_ResponseT", bound=BaseModel)


def read_reflection(
    payload: MicroextractionInput,
    *,
    provider: LLMProvider,
    limits: PipelineConfig,
    factory: NodeFactory | None = None,
) -> ExtractionOutcome:
    """
    Read an episode closely and record everything in it.

    One call does findings, events and cause-and-effect together. Splitting
    them would mean two separate readings of the same paragraph that can
    disagree with each other — a chain describing a moment that no finding
    records, or two accounts of the same feeling. They describe the same
    material and so they are read at once.

    The thing that names nodes can be handed in, because an episode read
    more than once must keep counting where it left off. Two readings that
    each started from one would hand out the same names twice.
    """
    prompt = REFLECTION_PROMPT.format(
        type_dictionary=render_type_dictionary(),
        high_signal_types=render_high_signal_types(),
        people=render_people(payload.coreference_map),
        text=payload.episode.cleaned_text,
    )
    response = request(
        provider=provider,
        prompt=prompt,
        response_model=ReflectionExtractionResponse,
        pass_name="reflection",
    )
    if response is None:
        return _nothing_extracted()

    return assemble(
        validate_reflection(response, reflection_context(payload, limits)),
        factory=factory or NodeFactory(payload, extraction_model=provider.model_name),
        with_anchor=True,
    )


def read_raw_capture(
    payload: MicroextractionInput,
    *,
    provider: LLMProvider,
    limits: PipelineConfig,
    factory: NodeFactory | None = None,
) -> ExtractionOutcome:
    """
    Take the little there is from a thin entry.

    No cause and effect, no anchor, and no feeling unless the person put
    one into words. An entry reaches this path because there was not enough
    in it to read closely, and reading closely anyway is how a shrug
    becomes a diagnosis in someone's permanent history.
    """
    response = request(
        provider=provider,
        prompt=RAW_CAPTURE_PROMPT.format(text=payload.episode.cleaned_text),
        response_model=RawCaptureResponse,
        pass_name="raw_capture",
    )
    if response is None:
        return _nothing_extracted()

    context = build_context(
        episode_text=payload.episode.cleaned_text,
        coreference_map=payload.coreference_map,
        raw_capture=True,
        limits=limits,
    )
    outcome = assemble(
        validate_raw_capture(response, context),
        factory=factory or NodeFactory(payload, extraction_model=provider.model_name),
        with_anchor=False,
    )
    # On this path every note is a loss and none of them will be asked
    # about again: the only two things it can produce are the topic and a
    # feeling the person stated, so a note means one of those two did not
    # survive.
    return outcome.model_copy(update={"abandoned": len(outcome.drops)})


# ---------------------------------------------------------------------------
# The shared parts
# ---------------------------------------------------------------------------


def reflection_context(
    payload: MicroextractionInput, limits: PipelineConfig
) -> ValidationContext:
    """
    Build the facts a close reading is judged against.

    Shared with the correction step, so a corrected item is held to exactly
    the same standard as a first-attempt one — the same permitted
    categories, the same known people, the same text to check quotes
    against.
    """
    return build_context(
        episode_text=payload.episode.cleaned_text,
        coreference_map=payload.coreference_map,
        raw_capture=False,
        limits=limits,
    )


def request(
    *,
    provider: LLMProvider,
    prompt: str,
    response_model: type[_ResponseT],
    pass_name: str,
) -> _ResponseT | None:
    """
    Ask the model once and insist on a readable answer.

    Returns nothing rather than raising, because the caller has something
    better to do with a failure than give up. Three things can go wrong and
    all three end the same way: the call failed, the reply was not JSON, or
    the JSON was not the shape that was asked for.

    Nothing about the entry is logged here — only which reading failed and
    why.
    """
    try:
        result = provider.generate_structured(
            prompt, response_model, system_instruction=SYSTEM_INSTRUCTION
        )
    except ProviderError as exc:
        _log_failure(pass_name, "provider_error", type(exc).__name__)
        return None

    if result.data is None:
        _log_failure(pass_name, "unparseable_response", result.parse_error)
        return None

    try:
        return response_model.model_validate(result.data)
    except ValidationError as exc:
        _log_failure(pass_name, "unexpected_shape", f"{exc.error_count()} field errors")
        return None


def assemble(
    report: ValidationReport,
    *,
    factory: NodeFactory,
    with_anchor: bool,
    attempt: int = 1,
) -> ExtractionOutcome:
    """
    Turn everything that survived checking into graph nodes.

    The anchor is only minted when something survived. An episode that
    produced nothing has nothing to anchor, and a session node standing
    alone would claim a piece of thinking happened that left no trace.
    """
    observations = factory.observations(report.observations, attempt=attempt)
    events = factory.events(report.events)
    chains, steps = factory.chains(report.chains)

    sessions = []
    if with_anchor and (observations or events or chains):
        sessions.append(factory.session_anchor(observations))

    return ExtractionOutcome(
        observations=tuple(observations),
        events=tuple(events),
        sessions=tuple(sessions),
        chains=tuple(chains),
        steps=tuple(steps),
        drops=report.drops,
        rejected=report.rejected,
        ungrounded=report.ungrounded,
        attempts=attempt,
    )


def _nothing_extracted() -> ExtractionOutcome:
    """The result of a reading that could not happen at all."""
    return ExtractionOutcome(used_fallback=True)


def _log_failure(pass_name: str, reason: str, detail: str | None = None) -> None:
    """Record that a reading failed, without recording what was being read."""
    logger.warning(
        "extraction could not read the episode",
        extra={"extraction_pass": pass_name, "reason": reason, "detail": detail},
    )


__all__ = ["read_reflection", "read_raw_capture", "request", "assemble", "reflection_context"]
