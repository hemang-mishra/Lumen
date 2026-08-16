"""
The instructions given to the language model when reading an episode.

Kept in one file so that changing what is asked for is a single visible
edit rather than a hunt through the code. Each template is filled in with
str.format, so a template can be read on its own without running anything.

The wording carries more weight here than anywhere else in the pipeline.
This step is the last one that can be checked against the person's actual
words; everything after it treats what comes out as though they had said
it. So the instructions repeatedly push in one direction: report what is
there, quote it, and leave gaps as gaps.
"""

from __future__ import annotations

from lumen.pipeline.extraction.catalog import EXCLUDED_TYPES
from lumen.pipeline.extraction.contracts import DropRule, RejectedItem
from lumen.schemas.enums import HIGH_SIGNAL_REQUIRED_TYPES
from lumen.schemas.pipeline import CoreferenceMap
from lumen.prompt_rules import AUTHOR_NAMING

SYSTEM_INSTRUCTION = (
    "You read personal journal entries and record what is actually in them. "
    "You have no knowledge of this person's past entries and must not "
    "imagine any. Record only what this entry supports, in their own terms, "
    "including anything unflattering, distressing or unresolved. Never "
    "diagnose, advise, soften or conclude on their behalf. If something is "
    "not there, leave it out. " + AUTHOR_NAMING + " Return only the "
    "requested structure."
)


REFLECTION_PROMPT = """\
Below is one episode from a journal entry — a single topic, already \
separated out from the rest. Read it closely and record what is in it.

Return three things: findings, events, and cause-and-effect sequences.

HOW MUCH TO RECORD

Record everything the episode supports, not a summary of it. Someone who \
wrote at length about one evening has said many separate things, and a \
handful of findings from a long piece of writing means most of what they \
said was thrown away.

Work through the episode in order rather than stepping back and summarising \
it. Each distinct thing they noticed, felt, realised, feared, resented, \
noticed themselves doing, or decided is its own finding, even when several \
sit in one sentence. Do not merge two points because they are related — "I \
felt behind" and "I compared myself to Alex" are two findings, not one \
tidier one about comparison.

Prefer the specific to the general. "The comparing is what hurts, not the \
gap" is worth recording; "he has some difficult feelings" is not, because it \
could be said of anybody.

This is not licence to invent. Every finding still needs its quote, and \
something not in the episode is still left out. Being thorough means \
noticing more of what is there, never reading more into it.

FINDINGS (observations)

Each finding is one standalone point. Give every finding a type from the \
list below, and use nothing outside that list. If something does not fit any \
type, leave it out rather than forcing it or inventing a name.

{type_dictionary}

For each finding also give:
  - content: the point itself, in one or two sentences, in their own terms.
  - raw_evidence: one or more short quotes taken word for word from the \
episode that support it. If you cannot quote the episode for it, do not \
record it.
  - extraction_signal_strength: STANDARD normally. HIGH when the moment \
carries unusual weight — an involuntary reaction, a realisation that shifts \
how they see themselves. CRITICAL only for something life-defining.
  - extraction_confidence: STANDARD normally. RECONSTRUCTIVE when they are \
recalling something from long ago rather than describing it fresh.
  - provenance: USER_GENERATED for their own thinking. AI_GENERATED for a \
question or framing that came from the assistant and that they have not taken \
up.
  - person_ref: the name of the person involved, if there is one, using the \
canonical name from the reference list below. Use null when no specific \
person is involved. Never invent a name and never guess at one.

These types have a required weight: {high_signal_types} must be marked HIGH \
or CRITICAL, never STANDARD.

A PATTERN must be written as three things together: the behaviour, what \
triggers it, and the internal state that goes with it. A pattern missing any \
of those is not usable.

EVENTS

Record an event when the person describes something that actually happened — \
an action taken, a conversation held, something that occurred to them. Give a \
one-sentence summary, the quotes describing it, and anyone involved. Do not \
record a feeling or a realisation as an event. If nothing happened in this \
episode and it is purely reflective, return an empty list.

CAUSE AND EFFECT

Where the episode describes one thing leading to another, record the \
sequence as ordered steps. Each step is one of:
  TRIGGER — what set it off
  INTERNAL_STATE — how they felt or what they thought at that point
  ACTION — what they did
  OUTCOME — what resulted
  LESSON — what they took from it

A sequence can hold several INTERNAL_STATE steps, before and after an action. \
Number the steps from 1 in the order they happened. If one action led to two \
different outcomes, give the parallel paths different branch_id values. Set \
is_anticipatory to true when the sequence is something they fear or imagine \
rather than something that happened.

Record a sequence wherever the episode contains one, not just the clearest \
one. An evening's writing usually holds more than a single thread — what set \
something off, what they felt, what they did about it, how it left them — and \
each of those threads is its own sequence.

Follow a feeling as it changes. "I felt X, then after that Y, and by the end \
Z" is one sequence with three INTERNAL_STATE steps, and recording only the \
last one keeps the destination and loses the journey, which is the part that \
explains them to themselves later.

Only record a sequence when the episode actually connects the steps. Do not \
assemble one out of separate points that happen to sit near each other. A \
sequence needs at least two steps; a single step is a finding, not a chain.

PEOPLE MENTIONED IN THIS ENTRY

{people}

WHAT NOT TO DO

Do not interpret, diagnose, or explain them to themselves. Do not resolve \
things they left open — an unresolved question is recorded as an OPEN_LOOP \
finding, not answered. Do not merge two separate points into one tidier one. \
Do not use anything you think you know about this person from outside this \
episode; you have not seen their other entries and must not act as if you \
have.

EPISODE:
{text}
"""


RAW_CAPTURE_PROMPT = """\
Below is a short or unclear journal entry. Very little is to be taken from \
it, on purpose — there is not enough here to support a deeper reading, and \
reading into it would put words in someone's mouth.

Return exactly two things:

1. context: one plain sentence saying what the entry is about on the \
surface. Describe only what they mention. Do not interpret it, do not \
explain why, and do not draw anything out of it.

2. emotion: a feeling, but only if they named one themselves. If they did, \
give the feeling in one short phrase, and put their exact words naming it in \
emotion_quote, copied word for word from the entry.

If they did not name a feeling, set both emotion and emotion_quote to null. \
Do not work a feeling out from the situation, the tone, or the wording. A \
tired-sounding entry is not the same as someone saying they are tired, and \
guessing here is exactly what this path exists to avoid.

ENTRY:
{text}
"""


CORRECTION_PROMPT = """\
Some of what you returned could not be used. Each item below is shown with \
what was wrong with it.

Return corrected versions of these items only. Do not add anything new, do \
not touch anything that is not listed here, and change only the part that was \
wrong — the substance of each item was accepted, only the way it was labelled \
was not.

If an item cannot be corrected honestly — the entry does not support it, or \
no available option fits — leave it out of your answer. Leaving it out is a \
correct answer and is better than forcing one.

Return them in the same order and under the same headings you used before.

{items}
{type_dictionary}
EPISODE:
{text}
"""


# What to tell the model about each way an item can be refused. Each line
# names the field at fault and what a usable answer looks like, and none of
# them restate the item's meaning — the model is fixing a label, not being
# asked to argue for the finding again.
_RULE_GUIDANCE: dict[DropRule, str] = {
    DropRule.UNKNOWN_TYPE: (
        'the type "{detail}" is not one of the available types. Use the '
        "closest type from the list below."
    ),
    DropRule.UNKNOWN_ENUM_VALUE: (
        "one of its settings is not a value that exists ({detail}). "
        "Use one of the values named in the original instructions."
    ),
    DropRule.SIGNAL_FLOOR: (
        "the type {detail} always carries unusual weight, so its "
        "extraction_signal_strength must be HIGH or CRITICAL, never STANDARD. "
        "Either raise the weight or choose a type that fits an ordinary moment."
    ),
    DropRule.UNKNOWN_STEP_TYPE: (
        'one of its steps has the type "{detail}", which does not exist. Every '
        "step must be TRIGGER, INTERNAL_STATE, ACTION, OUTCOME or LESSON."
    ),
    DropRule.EMPTY_CONTENT: "it arrived with nothing written in it.",
}


def render_correction_items(rejections: tuple[RejectedItem, ...]) -> str:
    """
    Write out each refused item with the reason it was refused.

    Grouped under the same headings the model answered with, so the
    corrected items come back in a shape that can be matched to what they
    are correcting without asking the model to track ids.
    """
    headings = {
        "observation": "FINDINGS THAT NEED CORRECTING",
        "event": "EVENTS THAT NEED CORRECTING",
        "chain": "SEQUENCES THAT NEED CORRECTING",
    }
    sections = []
    for kind, heading in headings.items():
        of_kind = [item for item in rejections if item.item_kind == kind]
        if not of_kind:
            continue
        lines = [
            f"{position}. Problem: {_explain(item)}\n   You returned: "
            f"{item.payload.model_dump_json()}"
            for position, item in enumerate(of_kind, start=1)
        ]
        sections.append(f"{heading}\n" + "\n".join(lines))
    return "\n\n".join(sections)


def _explain(rejection: RejectedItem) -> str:
    """Say what was wrong with one item, in terms of the field at fault."""
    template = _RULE_GUIDANCE.get(
        rejection.rule, "it did not pass the rules for this kind of item."
    )
    return template.format(detail=rejection.detail)


def needs_type_dictionary(rejections: tuple[RejectedItem, ...]) -> bool:
    """
    Whether the list of types should be repeated for this correction.

    Only when a type was the problem — which is usually because the model
    did not use the list the first time. Repeating it for an unrelated
    mistake would spend a large part of the prompt saying nothing new.
    """
    return any(
        item.rule in {DropRule.UNKNOWN_TYPE, DropRule.SIGNAL_FLOOR}
        for item in rejections
    )


def render_people(coreference_map: CoreferenceMap) -> str:
    """
    List the people already identified in the entry, for the prompt.

    Given so the model uses the settled name for someone rather than
    whichever nickname happened to fall inside this episode, which is what
    keeps one person from being recorded as two.

    References that could not be settled are listed as unsettled rather
    than left out. Hiding the ambiguity does not remove it; it just means
    the model meets the ambiguous phrase with no warning and picks one,
    confidently and unaccountably.
    """
    lines = [
        f"  - {entity.resolved_to} (also referred to as \"{entity.span}\")"
        for entity in coreference_map.resolved_entities
    ]
    lines += [
        f'  - "{ref.span}" is unclear — it may be any of: '
        + ", ".join(ref.candidates)
        + ". Do not choose one."
        for ref in coreference_map.ambiguous_refs
    ]
    if not lines:
        return (
            "No people have been identified in this entry. "
            "Use a name only if the episode states one."
        )
    return "\n".join(lines)


def render_high_signal_types() -> str:
    """
    Name the types that must never be recorded as ordinary.

    Types the extraction cannot produce at all are left out, so the
    instruction never mentions something the model was not offered.
    """
    return ", ".join(
        sorted(
            member.value
            for member in HIGH_SIGNAL_REQUIRED_TYPES
            if member not in EXCLUDED_TYPES
        )
    )


__all__ = [
    "SYSTEM_INSTRUCTION",
    "REFLECTION_PROMPT",
    "RAW_CAPTURE_PROMPT",
    "render_people",
    "render_high_signal_types",
    "CORRECTION_PROMPT",
    "render_correction_items",
    "needs_type_dictionary",
]
