"""
Checking what the model said before any of it becomes permanent.

Two ideas run through this file.

The first is that checking happens one item at a time. A reply holding
nine good findings and one with an invented category name should lose the
one, not the ten. So every rule here judges a single item and returns
either a cleaned-up version of it or a note saying why it was thrown away,
and the caller keeps going either way.

The second is that a rule either rejects an item or repairs it, never
guesses. Where something is unrecognisable it goes; where something is
merely untidy — steps numbered oddly, a list longer than allowed — it is
straightened out and kept. What is never done is filling in a missing
piece with something plausible, because at this point in the pipeline
plausible and true are indistinguishable and only one of them is wanted.

What comes out of here is not yet graph nodes. It is the same information
with every loose category name resolved to a real one, ready for the step
that gives things names and timestamps.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TypeVar

from lumen.config import PipelineConfig
from lumen.pipeline.extraction.catalog import EXCLUDED_TYPES, allowed_types
from lumen.pipeline.extraction.contracts import (
    DropRecord,
    DropRule,
    ExtractedCausalChain,
    ExtractedEvent,
    ExtractedObservation,
    RawCaptureResponse,
    ReflectionExtractionResponse,
)
from lumen.schemas.enums import (
    HIGH_SIGNAL_REQUIRED_TYPES,
    CausalStepType,
    ExtractionConfidence,
    ObservationType,
    Provenance,
    SignalStrength,
)
from lumen.schemas.pipeline import CoreferenceMap

logger = logging.getLogger(__name__)

_EnumT = TypeVar("_EnumT", bound=StrEnum)
_RawT = TypeVar("_RawT")
_CleanT = TypeVar("_CleanT")

# Words and punctuation are flattened before any text comparison, so that a
# quote differing only in spacing or a trailing full stop still counts as
# found in the entry.
_NON_WORD = re.compile(r"[^a-z0-9]+")


# ---------------------------------------------------------------------------
# The cleaned-up shapes that come out of checking
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CleanObservation:
    """One finding, with every category resolved and every rule satisfied."""

    type: ObservationType
    content: str
    provenance: Provenance
    signal_strength: SignalStrength
    extraction_confidence: ExtractionConfidence
    person_refs: tuple[str, ...] = ()
    raw_evidence: tuple[str, ...] = ()
    grounded: bool = True


@dataclass(frozen=True)
class CleanEvent:
    """Something that happened, checked."""

    event_summary: str
    signal_strength: SignalStrength
    person_refs: tuple[str, ...] = ()
    raw_evidence: tuple[str, ...] = ()
    grounded: bool = True


@dataclass(frozen=True)
class CleanStep:
    """One link of a cause-and-effect sequence, checked."""

    step_type: CausalStepType
    content: str
    branch_id: str | None = None


@dataclass(frozen=True)
class CleanChain:
    """A cause-and-effect sequence whose every step survived checking."""

    chain_summary: str
    is_anticipatory: bool
    steps: tuple[CleanStep, ...]


@dataclass(frozen=True)
class ValidationReport:
    """
    Everything that survived, and everything that did not.

    Attributes:
        observations: Findings that passed every rule.
        events: Events that passed every rule.
        chains: Sequences whose steps all passed.
        drops: One note per item thrown away, or per part removed from an
            item that was otherwise kept.
        ungrounded: How many kept items quoted evidence that could not be
            found in the entry.
    """

    observations: tuple[CleanObservation, ...] = ()
    events: tuple[CleanEvent, ...] = ()
    chains: tuple[CleanChain, ...] = ()
    drops: tuple[DropRecord, ...] = ()
    ungrounded: int = 0


@dataclass(frozen=True)
class ValidationContext:
    """
    What the rules need in order to judge an item.

    Built once per episode and passed to every rule, so no rule has to
    reach for anything on its own.

    Attributes:
        permitted: The categories this episode is allowed to produce. A
            thin entry gets a much shorter list than a full reflection.
        known_people: Names that actually appear in this entry, flattened
            for comparison. A name outside this set was invented.
        searchable_text: The episode text, flattened, for checking quotes
            against.
        limits: The ceilings on how much one episode may produce.
    """

    permitted: frozenset[ObservationType]
    known_people: frozenset[str]
    searchable_text: str
    limits: PipelineConfig


@dataclass
class _Collector:
    """
    Gathers drop notes while a reply is worked through.

    Exists so the rules can stay simple functions that return an item or
    nothing: the bookkeeping of what was lost lives here instead of being
    threaded through every return value.
    """

    drops: list[DropRecord] = field(default_factory=list)
    ungrounded: int = 0

    def drop(self, kind: str, index: int, rule: DropRule, detail: str = "") -> None:
        """Record that an item, or part of one, did not survive."""
        self.drops.append(
            DropRecord(item_kind=kind, index=index, rule=rule, detail=detail)
        )


# ---------------------------------------------------------------------------
# Building the context
# ---------------------------------------------------------------------------


def flatten(text: str) -> str:
    """
    Reduce text to lowercase words separated by single spaces.

    Used for every text comparison in this file. Someone's entry and a
    model's quote of it will differ in capitalisation, spacing and
    punctuation constantly; none of those differences mean the quote was
    made up, so none of them should count.
    """
    return f" {_NON_WORD.sub(' ', text.lower()).strip()} "


def build_context(
    *,
    episode_text: str,
    coreference_map: CoreferenceMap,
    raw_capture: bool,
    limits: PipelineConfig,
) -> ValidationContext:
    """
    Work out what this episode is allowed to produce and who it may name.

    Names are gathered from two places: the people already resolved for the
    whole entry, and the entry text itself. Both are needed — someone
    mentioned once by name never shows up in the resolved list, because
    there was no pronoun to resolve.
    """
    names = {
        flatten(entity.resolved_to).strip()
        for entity in coreference_map.resolved_entities
    }
    for ref in coreference_map.ambiguous_refs:
        names.update(flatten(candidate).strip() for candidate in ref.candidates)
    names.discard("")

    return ValidationContext(
        permitted=allowed_types(raw_capture=raw_capture),
        known_people=frozenset(names),
        searchable_text=flatten(episode_text),
        limits=limits,
    )


# ---------------------------------------------------------------------------
# The entry points
# ---------------------------------------------------------------------------


def validate_reflection(
    response: ReflectionExtractionResponse, context: ValidationContext
) -> ValidationReport:
    """Check everything taken from an entry that was read closely."""
    collector = _Collector()

    limits = context.limits
    within_limit = _cap(
        response.observations,
        limits.max_observations_per_episode,
        "observation",
        collector,
    )
    observations = _check_all(
        within_limit,
        lambda item, index: _check_observation(item, index, context, collector),
    )
    events = _check_all(
        response.events,
        lambda item, index: _check_event(item, index, context, collector),
    )
    chains = _check_all(
        _cap(
            response.causal_mechanisms,
            limits.max_causal_chains_per_episode,
            "chain",
            collector,
        ),
        lambda item, index: _check_chain(item, index, context, collector),
    )

    return ValidationReport(
        observations=observations,
        events=events,
        chains=chains,
        drops=tuple(collector.drops),
        ungrounded=collector.ungrounded,
    )


def validate_raw_capture(
    response: RawCaptureResponse, context: ValidationContext
) -> ValidationReport:
    """
    Check the little taken from a thin entry.

    The feeling is treated separately from the topic, and held to a
    stricter test: it survives only if the person's own words naming it can
    be found in the entry. A feeling nobody put into words is one the model
    worked out for itself, which is the single thing this path exists to
    prevent.
    """
    collector = _Collector()
    kept: list[CleanObservation] = []

    topic = response.context.strip()
    if topic:
        kept.append(_plain_finding(ObservationType.CONTEXT, topic))
    else:
        collector.drop("observation", 0, DropRule.EMPTY_CONTENT, "context")

    feeling = _check_stated_feeling(response, context, collector)
    if feeling is not None:
        kept.append(feeling)

    return ValidationReport(
        observations=tuple(kept),
        drops=tuple(collector.drops),
        ungrounded=collector.ungrounded,
    )


def _check_stated_feeling(
    response: RawCaptureResponse,
    context: ValidationContext,
    collector: _Collector,
) -> CleanObservation | None:
    """
    Keep a feeling only if the person put it into words themselves.

    The model is made to hand back the words it is relying on, and those
    words are looked for in the entry. That turns "do not work out how they
    felt" from an instruction it can quietly ignore into something that can
    be checked: a tired-sounding entry is not the same as someone saying
    they are tired, and this is the one path with too little text to tell
    the difference.
    """
    feeling = (response.emotion or "").strip()
    if not feeling:
        return None

    quote = (response.emotion_quote or "").strip()
    if not quote:
        collector.drop("observation", 1, DropRule.QUOTE_NOT_FOUND, "no quote given")
        return None
    if not _quote_found(quote, context):
        collector.drop("observation", 1, DropRule.QUOTE_NOT_FOUND, "quote not in entry")
        return None

    return _plain_finding(ObservationType.EMOTION, feeling, evidence=(quote,))


def _plain_finding(
    kind: ObservationType, content: str, *, evidence: tuple[str, ...] = ()
) -> CleanObservation:
    """
    Build an ordinary finding of the person's own.

    Used on the thin path, where none of the weighing that a close reading
    does applies: everything is the person's own, ordinary, and described
    fresh rather than recalled.
    """
    return CleanObservation(
        type=kind,
        content=content,
        provenance=Provenance.USER_GENERATED,
        signal_strength=SignalStrength.STANDARD,
        extraction_confidence=ExtractionConfidence.STANDARD,
        raw_evidence=evidence,
    )


# ---------------------------------------------------------------------------
# One rule at a time
# ---------------------------------------------------------------------------


def _check_observation(
    raw: ExtractedObservation,
    index: int,
    context: ValidationContext,
    collector: _Collector,
) -> CleanObservation | None:
    """Judge one finding, returning it cleaned up or nothing at all."""
    content = raw.content.strip()
    if not content:
        collector.drop("observation", index, DropRule.EMPTY_CONTENT)
        return None

    kind = _as_member(ObservationType, raw.type)
    if kind is None:
        collector.drop("observation", index, DropRule.UNKNOWN_TYPE, raw.type[:40])
        return None
    if kind in EXCLUDED_TYPES:
        collector.drop("observation", index, DropRule.EXCLUDED_TYPE, kind.value)
        return None
    # A second line of defence rather than an expected outcome. A thin entry
    # is normally read by a prompt that cannot return a deeper category at
    # all, so this catches only the case where the wrong reading was run
    # over a thin entry — the one mistake that would let a shrug become a
    # diagnosis in someone's permanent history.
    if kind not in context.permitted:
        collector.drop("observation", index, DropRule.TYPE_NOT_ALLOWED_HERE, kind.value)
        return None

    provenance = _as_member(Provenance, raw.provenance)
    signal = _as_member(SignalStrength, raw.extraction_signal_strength)
    confidence = _as_member(ExtractionConfidence, raw.extraction_confidence)
    if provenance is None or signal is None or confidence is None:
        collector.drop(
            "observation",
            index,
            DropRule.UNKNOWN_ENUM_VALUE,
            # Named as the model saw them, since this note exists to explain
            # a reply, not the code that read it.
            _name_unknown(
                provenance=(provenance, raw.provenance),
                extraction_signal_strength=(signal, raw.extraction_signal_strength),
                extraction_confidence=(confidence, raw.extraction_confidence),
            ),
        )
        return None

    # Some kinds of finding mark where the important material is, and are
    # boosted when the history is searched later. A model that reports one
    # as ordinary has contradicted itself; the finding is re-asked for
    # rather than quietly filed at the wrong weight.
    if kind in HIGH_SIGNAL_REQUIRED_TYPES and signal is SignalStrength.STANDARD:
        collector.drop("observation", index, DropRule.SIGNAL_FLOOR, kind.value)
        return None

    people = _check_people(
        [raw.person_ref] if raw.person_ref else [],
        index,
        "observation.person_ref",
        context,
        collector,
    )
    evidence = tuple(quote.strip() for quote in raw.raw_evidence if quote.strip())
    grounded = _check_grounding(evidence, context, collector)

    return CleanObservation(
        type=kind,
        content=content,
        provenance=provenance,
        signal_strength=signal,
        extraction_confidence=confidence,
        person_refs=people,
        raw_evidence=evidence,
        grounded=grounded,
    )


def _check_event(
    raw: ExtractedEvent,
    index: int,
    context: ValidationContext,
    collector: _Collector,
) -> CleanEvent | None:
    """Judge one event, returning it cleaned up or nothing at all."""
    summary = raw.event_summary.strip()
    if not summary:
        collector.drop("event", index, DropRule.EMPTY_CONTENT)
        return None

    signal = _as_member(SignalStrength, raw.signal_strength)
    if signal is None:
        collector.drop(
            "event", index, DropRule.UNKNOWN_ENUM_VALUE, raw.signal_strength[:40]
        )
        return None

    people = _check_people(
        raw.person_refs, index, "event.person_refs", context, collector
    )
    evidence = tuple(quote.strip() for quote in raw.raw_evidence if quote.strip())
    grounded = _check_grounding(evidence, context, collector)

    return CleanEvent(
        event_summary=summary,
        signal_strength=signal,
        person_refs=people,
        raw_evidence=evidence,
        grounded=grounded,
    )


def _check_chain(
    raw: ExtractedCausalChain,
    index: int,
    context: ValidationContext,
    collector: _Collector,
) -> CleanChain | None:
    """
    Judge one cause-and-effect sequence.

    A broken step takes the whole sequence with it. A sequence is a claim
    about order — this led to that, which led to the other — and a sequence
    with a hole in it makes a claim nobody made. Keeping the readable parts
    would quietly invent a shorter story than the one that was told.
    """
    summary = raw.chain_summary.strip()
    if not summary:
        collector.drop("chain", index, DropRule.EMPTY_CONTENT)
        return None

    ordered = sorted(raw.causal_chain, key=lambda step: step.step)
    if len(ordered) > context.limits.max_causal_steps_per_chain:
        kept = context.limits.max_causal_steps_per_chain
        collector.drop(
            "chain.steps", index, DropRule.OVER_LIMIT, f"kept {kept} of {len(ordered)}"
        )
        ordered = ordered[:kept]

    steps: list[CleanStep] = []
    for step in ordered:
        step_type = _as_member(CausalStepType, step.type)
        if step_type is None:
            collector.drop("chain", index, DropRule.UNKNOWN_STEP_TYPE, step.type[:40])
            return None
        content = step.content.strip()
        if not content:
            collector.drop("chain", index, DropRule.EMPTY_CONTENT, "step")
            return None
        steps.append(
            CleanStep(step_type=step_type, content=content, branch_id=step.branch_id)
        )

    # One step is a single point about the person, not a sequence of one
    # thing causing another. It has no chain to describe.
    if len(steps) < 2:
        collector.drop("chain", index, DropRule.CHAIN_TOO_SHORT, f"{len(steps)} steps")
        return None

    if [step.step for step in ordered] != list(range(1, len(ordered) + 1)):
        logger.warning(
            "causal chain steps were renumbered",
            extra={"chain_index": index, "step_count": len(steps)},
        )

    return CleanChain(
        chain_summary=summary,
        is_anticipatory=raw.is_anticipatory,
        steps=tuple(steps),
    )


def _check_people(
    names: list[str],
    index: int,
    kind: str,
    context: ValidationContext,
    collector: _Collector,
) -> tuple[str, ...]:
    """
    Keep only names that actually appear in the entry.

    An unrecognised name is removed, but the item it was attached to is
    kept. The statement itself is very often true and only the name is
    wrong, and losing a real observation in order to shed a wrong name is
    the worse trade of the two. Left in, though, an invented name later
    becomes an invented person in the graph.
    """
    kept: list[str] = []
    for name in names:
        cleaned = name.strip()
        if not cleaned:
            continue
        if _is_known_person(cleaned, context):
            kept.append(cleaned)
        else:
            collector.drop(kind, index, DropRule.UNKNOWN_PERSON)
    return tuple(kept)


def _check_grounding(
    evidence: tuple[str, ...], context: ValidationContext, collector: _Collector
) -> bool:
    """
    Note whether an item's quotes can be found in the entry.

    Counted rather than acted on. An entry translated from another language
    is quoted in words the person never literally used, so treating a
    missing quote as proof of invention would throw away perfectly real
    findings from every translated entry. What the count is for is watching
    the rate: quotes that stop matching are what invention looks like from
    the outside, and that is the signal to tighten this into a rejection.
    """
    if evidence and any(_quote_found(quote, context) for quote in evidence):
        return True
    collector.ungrounded += 1
    return False


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------


def _as_member(enum_cls: type[_EnumT], value: str | None) -> _EnumT | None:
    """
    Turn a name from the model into a real category, or nothing.

    Returning nothing rather than raising is what lets one unrecognisable
    name cost one item instead of the whole reply.
    """
    if not value:
        return None
    try:
        return enum_cls(value.strip().upper())
    except ValueError:
        return None


def _name_unknown(**checks: tuple[object | None, str]) -> str:
    """Name the fields whose values were not recognised, for the drop note."""
    return ", ".join(
        f"{field_name}={raw[:20]}"
        for field_name, (parsed, raw) in checks.items()
        if parsed is None
    )


def _is_known_person(name: str, context: ValidationContext) -> bool:
    """True when this name was resolved for the entry or appears in its text."""
    flat = flatten(name).strip()
    if not flat:
        return False
    if flat in context.known_people:
        return True
    return f" {flat} " in context.searchable_text


def _quote_found(quote: str, context: ValidationContext) -> bool:
    """True when a quote can be located in the episode text."""
    flat = flatten(quote).strip()
    if not flat:
        return False
    return f" {flat} " in context.searchable_text


def _cap(
    items: list[_RawT], limit: int, kind: str, collector: _Collector
) -> list[_RawT]:
    """
    Hold a list to its ceiling, recording what was cut.

    The model is asked for the most significant items first, so the tail is
    the cheapest part to lose when a reply comes back far longer than any
    single episode should produce.
    """
    if len(items) <= limit:
        return items
    collector.drop(kind, limit, DropRule.OVER_LIMIT, f"kept {limit} of {len(items)}")
    return items[:limit]


def _check_all(
    items: list[_RawT], check: Callable[[_RawT, int], _CleanT | None]
) -> tuple[_CleanT, ...]:
    """Run a rule over every item, keeping whatever survives."""
    checked = (check(item, index) for index, item in enumerate(items))
    return tuple(item for item in checked if item is not None)


__all__ = [
    "CleanObservation",
    "CleanEvent",
    "CleanStep",
    "CleanChain",
    "ValidationReport",
    "ValidationContext",
    "build_context",
    "validate_reflection",
    "validate_raw_capture",
    "flatten",
]
