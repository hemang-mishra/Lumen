"""
The instructions given to the language model at each step.

Kept together in one file so that changing what the model is asked is a
single, visible edit rather than a hunt through the code. Each template is
filled in with str.format, so the placeholders are obvious and a template
can be read on its own without running anything.

The wording matters more than it looks. These prompts run over people's
private writing about their own lives, and an instruction that invites the
model to improve, interpret or tidy up what was said will quietly put words
into someone's history that they never wrote.
"""

from __future__ import annotations
from lumen.prompt_rules import AUTHOR_NAMING

SYSTEM_INSTRUCTION = (
    "You prepare personal journal entries for analysis. Your job is to make "
    "what the person said legible, never to improve it, interpret it, or add "
    "to it. Preserve their meaning exactly, including anything unflattering, "
    "distressing or unresolved. Never soften, censor or conclude on their "
    "behalf. " + AUTHOR_NAMING + " Return only the requested structure."
)


CONVERSATION_PROMPT = """\
Below is a conversation between a person and an assistant. Read the whole \
thing, then do two things.

First, classify every message written by the person (role USER). Ignore the \
assistant's messages for classification purposes.
  - OPERATIONAL_REQUEST: they are asking the system to do or fetch something, \
with no reflection about themselves. Example: "what did I say last Tuesday?"
  - EXPRESSIVE: they are saying something about their own experience, \
feelings, thoughts or life.

An emotionally charged or rhetorical question is EXPRESSIVE, not \
OPERATIONAL_REQUEST. "What is wrong with my brain?" is someone in distress, \
not someone querying a database. When unsure, choose EXPRESSIVE.

Also set co_created_marker to true on any message where the person explicitly \
takes up something the assistant just said — "I love that framing", "I'm \
going to use that", "yes, exactly that".

For every such moment, add the assistant's own wording that was taken up to \
co_created_spans, quoted exactly as the assistant wrote it. Quote the phrase \
or sentence carrying the idea, not the whole message. Leave the list empty if \
the person took up nothing. Do not add the person's own words to this list — \
only the assistant's.

Second, condense every message written by the assistant (role AI) into one \
or two sentences saying what it actually said, in assistant_digests, keyed by \
that message's id. Keep any question it asked, because the person's next \
message is usually an answer to it and reads as nonsense without it. Drop the \
restating, the encouragement, and the offers of further help. Do not condense \
the person's messages — those are kept exactly as written and are not your \
concern here.

Third, write a session summary of what the conversation settled on: the \
conclusions the person arrived at and still held by the end. This is a record \
of where the evening landed, not a replacement for it — everything they wrote \
is kept regardless, so do not worry about leaving something out of the \
summary. Write it in their voice, first person. If nothing was settled, \
return an empty summary.

CONVERSATION:
{dialogue}
"""


DIALOGUE_STRUCTURE_PROMPT = """\
Below is a conversation, one numbered turn at a time. Split it into \
conceptual episodes and resolve who its pronouns refer to.

SPLITTING

Give each episode the numbers of the turns that belong to it. Do not repeat \
the text — the numbers are enough, and they are how the writing is put back \
together afterwards.

Split by subject matter, not by time. A conversation wanders off a subject \
and returns to it; both parts are the same episode even with other turns \
between them. Turn numbers therefore need not be contiguous.

Split finely. An evening spent on work stress, a friend leaving, and an old \
memory that surfaced is three episodes, not one — and if the work stress \
covers both a specific argument and a longer-standing worry about the job, \
that is two. Each episode should be one thing that could be thought about on \
its own. A conversation of this length usually holds several; returning one \
episode for the whole of it is almost always wrong.

Every numbered turn must appear in exactly one episode. Give every episode a \
one-line summary of what it is about, and any broad themes it covers, such as \
"Work Satisfaction" or "Social Dynamics".

If the person anchors an episode to a named period of their past — "back \
during my exam prep years", "when I was living alone" — record that as \
historical_era. Leave it null otherwise. Do not invent one.

REFERENCES

Resolve pronouns and nicknames to the person they refer to, within this \
conversation only.
  - A pronoun resolves to the most recent clearly named person.
  - A nickname or role resolves to a name established earlier here: "J" to \
"Jordan", "my manager" to "Neha" if that was stated.
  - If a reference could plausibly be two different people, do not choose. \
Put it in ambiguous_refs with both candidates and why it is unclear. A wrong \
confident answer here attaches someone's words to the wrong person.

Only resolve references you can settle from this conversation alone. You have \
no knowledge of any other entry, and you must not assume one.

THE CONVERSATION, TURN BY TURN:
{dialogue}
"""


NORMALIZE_VOICE_PROMPT = """\
Below is a transcript of someone speaking a journal entry aloud. Clean it \
into readable English without changing what they meant.

1. Remove hesitation fillers that carry no meaning: "like" used as filler, \
"you know", "right" as a trailing tag, "basically" as an opener, "literally" \
when it modifies nothing.
   Keep any of these words when they carry real weight. "Right, so the issue \
was..." is them organising their thoughts and must stay. "I like this" is a \
verb and must stay.

2. Resolve spoken self-corrections, keeping only the correction:
   "He was really supportive, wait no actually he wasn't, he just stayed \
quiet" becomes "He just stayed quiet".
   "I think I was angry — or rather, I was scared" becomes "I was scared".
   But if the false start carries information that the correction does not, \
keep both and mark the abandoned part: "[CORRECTED_FROM: I was angry] I was \
scared". Losing a genuine first reaction is worse than keeping an untidy \
sentence.

3. Translate any non-English text into English, including sentences that mix \
languages. Translate a mixed sentence as a whole so it still reads as one \
thought. List every language you found in detected_languages using short \
codes such as "en" or "hi", and set translated to true if you translated \
anything.

Do not summarise. Do not reorder. Do not add anything they did not say. If \
the text is already clean English, return it unchanged.

TRANSCRIPT:
{text}
"""


NORMALIZE_TEXT_PROMPT = """\
Below is a journal entry someone typed. Return it as readable English \
without changing what they meant.

Translate any non-English text into English, including sentences that mix \
languages. Translate a mixed sentence as a whole so it still reads as one \
thought. List every language you found in detected_languages using short \
codes such as "en" or "hi", and set translated to true if you translated \
anything.

This was typed, not spoken, so every word was chosen. Do not remove \
hesitations, do not resolve what look like corrections, and do not tidy the \
phrasing. Do not summarise, reorder, or add anything. If the text is already \
English, return it unchanged.

ENTRY:
{text}
"""


STRUCTURE_PROMPT = """\
Below is a cleaned journal entry. Split it into conceptual episodes and \
resolve who its pronouns refer to.

SPLITTING

Split by subject matter, not by time or by paragraph. People write \
non-linearly: an entry may move from a morning argument, to a thought about \
work, and back to the morning again. Both parts about the morning belong to \
the same episode even though other writing sits between them.

Give every episode a one-line summary of what it is about, the text \
belonging to it, and any broad themes it covers, such as "Work Satisfaction" \
or "Social Dynamics".

If the person anchors an episode to a named period of their past — "back \
during my exam prep years", "when I was living alone" — record that as \
historical_era. Leave it null otherwise. Do not invent one.

If the entry is genuinely about one thing, return exactly one episode \
containing all of it. Do not split for the sake of splitting. Every word of \
the entry must appear in exactly one episode.

REFERENCES

Resolve pronouns and nicknames to the person they refer to, within this \
entry only.
  - A pronoun resolves to the most recent clearly named person.
  - A nickname or role resolves to a name established earlier in this same \
entry: "J" to "Jordan", "my manager" to "Neha" if that was stated here.
  - If a reference could plausibly be two different people, do not choose. \
Put it in ambiguous_refs with both candidates and why it is unclear. A wrong \
confident answer here attaches someone's words to the wrong person.

Only resolve references you can settle from this entry alone. You have no \
knowledge of any other entry, and you must not assume one.

ENTRY:
{text}
"""


TRIAGE_PROMPT = """\
Below are the episodes of one journal entry. Score each for how complete a \
piece of reflection it is, on a scale from 0.0 to 1.0:

  1.0 = A clear, complete thought with a subject, a feeling, and a context.
  0.5 = A partial thought. Some context is missing but the core meaning is \
legible.
  0.0 = Incoherent. There is no reflection here to work with.

Score what is there, not how significant it sounds. A calm, plainly written \
account of an ordinary day is a complete thought and scores high. A dramatic \
fragment that never says what happened is not.

Give a one-sentence reason for each score.

For any episode you score below {threshold}, also write exactly {count} \
questions that would help the person say more about it. Ask about what is \
missing. Make the questions specific to what they actually wrote — a generic \
question tells them you were not listening. Do not ask leading questions and \
do not offer interpretations of their experience. Leave reflection_prompts \
empty for episodes scoring at or above {threshold}.

EPISODES:
{episodes}
"""


REFLECTION_PROMPTS_PROMPT = """\
Below is a short journal entry — too short to analyse properly.

Write exactly {count} questions that would help the person say more about it. \
Ask about what is missing. Make the questions specific to what they actually \
wrote, so it is clear they were read. Do not ask leading questions, do not \
offer interpretations of their experience, and do not tell them how to feel.

ENTRY:
{text}
"""


def render_episodes_for_triage(texts: list[str]) -> str:
    """
    Lay episodes out as a numbered list for scoring.

    The numbers are how the scores are matched back to the episodes
    afterwards, so they start at 1 and match the episode positions exactly.
    """
    return "\n\n".join(
        f"--- EPISODE {index} ---\n{text}" for index, text in enumerate(texts, start=1)
    )


__all__ = [
    "SYSTEM_INSTRUCTION",
    "CONVERSATION_PROMPT",
    "DIALOGUE_STRUCTURE_PROMPT",
    "NORMALIZE_VOICE_PROMPT",
    "NORMALIZE_TEXT_PROMPT",
    "STRUCTURE_PROMPT",
    "TRIAGE_PROMPT",
    "REFLECTION_PROMPTS_PROMPT",
    "render_episodes_for_triage",
]
