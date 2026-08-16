"""
What the models are asked when deciding how today relates to the past.

Two prompts, and the difference between them is deliberate. The first asks a
fast model to read every finding against what the search brought back and
say how each one relates to it. The second takes only the answers that would
permanently change something long-held and asks a careful model to look
again before any of them counts.

Both are written to make the safe answer easy. A model asked "has this
person changed?" will find change, because that is the more interesting
answer; so the instructions say plainly that recording something separately
is the normal outcome, that noticing a habit once is not the same as
breaking it, and that a bad week is not a new person. The wording is doing
work here that no amount of checking afterwards could do — a check can
refuse an over-eager answer, but it cannot invent the careful one.
"""

from __future__ import annotations

from lumen.pipeline.reconciliation.contracts import DecisionItem
from lumen.schemas.enums import CandidateRetrievalSource
from lumen.schemas.pipeline import CandidateNode
from lumen.prompt_rules import AUTHOR_NAMING

SYSTEM_INSTRUCTION = (
    "You compare what someone noticed today against what they have recorded "
    "before, and say how the two relate. You never invent history, never "
    "claim a person has changed on the strength of a single day, and never "
    "guess at a match you cannot see evidence for. Preferring the cautious "
    "answer is correct. " + AUTHOR_NAMING + " Return only the requested "
    "structure."
)


ACTION_GUIDE = """\
MERGE       — today's finding says the same thing as an existing record.
REINFORCE   — it agrees with an existing record but is a separate occasion.
EVOLVE      — something previously settled has genuinely shifted. Needs a \
description of what changed.
BRANCH      — related to what exists, but its own thing. This is the normal \
answer, and the right one whenever you are unsure.
CONTRADICT  — the person holds this and an existing belief at the same time, \
and the two cannot both be true.
DIALECTIC   — this and an existing record oppose each other, and both are \
true. Nothing needs resolving.
REGULATE    — the person caught themselves in a known habit and interrupted \
it. Not the same as breaking it.
"""


DECISION_PROMPT = """\
Below are things someone noticed in one journal entry, numbered. Under each \
one are records from their own past that might be related.

For each numbered item, say how it relates to those records.

{action_guide}
Rules that matter more than they look:

- BRANCH is the safe answer and the common one. Recording something \
separately costs a little tidiness. Wrongly merging two ideas, or wrongly \
declaring that someone has changed, corrupts their history permanently.
- Some past records are shown as FOUND BY ANCHOR. Those surfaced because \
they involve the same person or the same period of life, not because they \
read alike — the words will often look unrelated. Judge them on whether \
today's entry continues, resolves or contradicts what they describe. Do not \
dismiss one for reading differently.
- EVOLVE means the person has changed, not that they had a good day or a bad \
one. Doing something once that they have long avoided is a moment worth \
recording on its own, not a new identity.
- If today's item describes an intense but short-lived stretch inside a \
longer period — a crunch week, an exam fortnight, an illness — set \
is_local_extremum to true. Their normal is not what those weeks look like.
- Give a second-best reading for every item. If two readings are genuinely \
close, say so honestly in the numbers rather than picking one.
- If nothing in the past is related, answer BRANCH with no target.

When you answer BRANCH and the item is a claim about how this person works, \
what they believe, or a habit they repeat, also give wording for the new \
record: a short name, the statement itself, and which part of life it \
belongs to (one of: {domains}).

If any item names a person, list those people at the end with how they relate \
to the writer and how the writer seems to feel about them, but only where the \
entry actually says.

ITEMS:
{items}
"""


ESCALATION_PROMPT = """\
A faster model read these items and concluded that each one permanently \
changes something this person has held for a long time. Those are the three \
hardest conclusions to undo, so they are checked before they count.

For each item below, decide whether that conclusion holds.

Confirm only if the entry itself supports it. Consider in particular:

- Is this a change, or is it one occasion? A single contrary moment, however \
vivid, is a moment. Say so by overruling to BRANCH.
- Is the earlier record really being superseded, or are both true at once? \
Two things that are both true and pull against each other are a tension to \
record, not a conflict to resolve.
- Is the wording of the earlier record actually about the same thing, or does \
it only sound similar?

You may confirm, lower the confidence, or overrule to a safer reading \
(BRANCH, REINFORCE or REGULATE). You may not raise an item to a heavier \
conclusion than the one it arrived with.

{action_guide}
When you confirm a change, give a plain description of what changed. When you \
confirm a clash or a tension, describe it in one sentence.

ITEMS:
{items}
"""


def render_items(items: list[DecisionItem]) -> str:
    """
    Lay out every finding with its candidate matches, numbered.

    The numbers are how answers are matched back to findings afterwards, so
    they start at 1 and follow the order the items were given in.
    """
    return "\n\n".join(
        _render_item(position, item)
        for position, item in enumerate(items, start=1)
    )


def _render_item(position: int, item: DecisionItem) -> str:
    """One finding and everything the search found for it."""
    kind = item.observation_type.value if item.observation_type else item.node_type
    lines = [
        f"{position}. [{kind}] {item.text}",
        f"   weight: {item.signal_strength.value}; origin: {item.provenance.value}",
    ]
    if item.candidates:
        lines.append("   past records:")
        lines.extend(f"     {_render_candidate(c)}" for c in item.candidates)
    else:
        lines.append("   past records: none found")
    return "\n".join(lines)


def _render_candidate(candidate: CandidateNode) -> str:
    """
    One past record, labelled with how it was found.

    How it was found is not decoration. A record that surfaced because a
    name matched deserves to be read on its own terms; one that surfaced
    because it reads alike has already been judged by resemblance once, and
    resemblance is the easier thing to over-trust.
    """
    if candidate.retrieval_source is CandidateRetrievalSource.STRUCTURAL:
        anchor = candidate.structural_anchor_type
        found = f"FOUND BY ANCHOR ({anchor.value if anchor else 'unknown'})"
    else:
        found = f"closeness {candidate.similarity_score:.2f}"
    return (
        f"- id={candidate.node_id} [{candidate.node_type}] {found}\n"
        f"       {candidate.content_preview}"
    )


def render_domains(domains: list[str]) -> str:
    """The list of life areas a new record may be filed under."""
    return ", ".join(domains)


__all__ = [
    "SYSTEM_INSTRUCTION",
    "ACTION_GUIDE",
    "DECISION_PROMPT",
    "ESCALATION_PROMPT",
    "render_items",
    "render_domains",
]
