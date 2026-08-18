"""
What the model is asked for when a report needs its sentences written.

There are two prompts. The long one turns a page of counts into the prose a
person actually reads; the short one describes a two-day burst of change.

Both are written around a single restriction, stated plainly and repeated:
produce no numbers and invent no identifiers. Everything countable has already
been counted before the model is called, and the one way this stage can damage
a report is by writing a confident sentence about something that did not
happen. The prompt asks for phrasing, not for findings.

The briefs are rendered rather than dumped. A model handed a whole quarter of
somebody's history writes about whatever is longest; a model handed a short,
ordered summary writes about what recurred.
"""

from __future__ import annotations

from lumen.prompt_rules import AUTHOR_NAMING

SYSTEM_INSTRUCTION = (
    "You write the prose for a periodic summary of one person's journal. "
    "Everything factual has already been counted and is given to you. Your "
    "job is wording, not analysis: name what the figures show, in the "
    "person's own register, without adding to them. Never state a number, a "
    "frequency, a date or a duration — those are filled in around your "
    "sentences. Never mention an identifier that is not in the material you "
    "were given. " + AUTHOR_NAMING + " Return only the requested structure."
)


NARRATIVE_PROMPT = """\
Below is what a {report_type} period of this person's journal contained, \
already counted. The period runs from {period_start} to {period_end}.

Write the wording for the summary. Specifically:

- headline: one or two sentences saying what this period was mostly about. \
Descriptive, not encouraging. If the period was thin, say that plainly.
- growth_area_label / growth_area_evidence: what the improving pattern \
represents, and one sentence on what appears to have shifted it. Leave both \
empty if no growth candidate is listed.
- struggle_label: what the most frequent pattern is really about, in the \
person's own terms. Leave empty if none is listed.
- relational_summaries: one sentence per person listed, on how that \
relationship read this period. Use the person_ref exactly as given.
- environment_groups: gather the environment notes into places. Several notes \
about the same place belong in one group even when they word it differently. \
Give each group the observation ids it came from, copied exactly.
- relationship_arcs: for each person listed, one sentence on where the \
relationship travelled, and a direction from: STRENGTHENING, STABLE, \
STRAINING, FADING. Use the person_id exactly as given.
- biographical_gaps: for each gap listed, a status from PRESENT, NARROWING, \
CLOSED, and one sentence of evidence when it is not PRESENT. Use the \
observation_id exactly as given.
- contradiction_prompts: for each tension listed, one question that helps the \
person look at it. Ask, do not resolve. Use the contradiction_id exactly.
- archetype_shift: only if a shift is marked as detected below. Give it a \
short name in the form "X → Y" and one sentence of evidence. Otherwise leave \
it out entirely.

Rules, and they matter more than completeness:

Do not write any number, count, percentage, date or span of time. They are \
inserted around your sentences and yours would contradict them.

Do not name an identifier that does not appear below. An id you invent \
silently attaches your sentence to nothing, and the sentence still reads as \
though it were about something real.

Say less rather than guessing. An empty field is read as "nothing to say \
here", which is a true and useful answer. A confident sentence about \
something that is not in the material below is not recoverable later.

MATERIAL:
{brief}
"""


SHADOW_SYSTEM_INSTRUCTION = (
    "You describe a short burst of change in one person's journal, from the "
    "record of what was decided about it. You are describing movement over "
    "the last couple of days, not summarising a period. Keep it to what the "
    "decisions show. " + AUTHOR_NAMING + " Return only the requested structure."
)


SHADOW_PROMPT = """\
In the last {hours} hours, this person's journal produced the decisions below. \
Each one is either something new branching away from what was there, or a \
tension recorded between two things they hold.

Write:

- shift_type: a short phrase naming the kind of movement this is.
- summary: one or two sentences on what appears to be moving.

Describe only what these decisions show. Do not state how many there were, do \
not predict what comes next, and do not tell the person what it means about \
them.

DECISIONS:
{decisions}
"""


def render_section(title: str, lines: list[str]) -> str:
    """
    One block of the brief, or nothing at all when it is empty.

    Empty sections are left out rather than shown as empty. A heading with
    nothing under it invites a model to fill it in, which is the one thing
    this stage is built to prevent.
    """
    if not lines:
        return ""
    body = "\n".join(f"  {line}" for line in lines)
    return f"{title}:\n{body}\n"


__all__ = [
    "SYSTEM_INSTRUCTION",
    "NARRATIVE_PROMPT",
    "SHADOW_SYSTEM_INSTRUCTION",
    "SHADOW_PROMPT",
    "render_section",
]
