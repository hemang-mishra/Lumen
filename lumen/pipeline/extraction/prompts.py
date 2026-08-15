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
from lumen.schemas.enums import HIGH_SIGNAL_REQUIRED_TYPES
from lumen.schemas.pipeline import CoreferenceMap

SYSTEM_INSTRUCTION = (
    "You read personal journal entries and record what is actually in them. "
    "You have no knowledge of this person's past entries and must not "
    "imagine any. Record only what this entry supports, in their own terms, "
    "including anything unflattering, distressing or unresolved. Never "
    "diagnose, advise, soften or conclude on their behalf. If something is "
    "not there, leave it out. Return only the requested structure."
)


REFLECTION_PROMPT = """\
Below is one episode from a journal entry — a single topic, already \
separated out from the rest. Read it closely and record what is in it.

Return three things: findings, events, and cause-and-effect sequences.

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
]
