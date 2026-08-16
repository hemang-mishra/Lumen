"""
The check that switches everything else off.

Almost every part of Lumen is allowed to be approximately right. This part is
not. When somebody is in real distress, putting their own psychological
history in front of the AI answering them is the single worst thing the
system can do — it turns a moment that needs presence into a moment that
gets analysed.

That judgement is made by a small fast model, and small fast models are
sometimes wrong. So there is a floor underneath it: a short list of phrases
that are not ambiguous in any reading, written in plain code. If one of them
appears, the turn is treated as a crisis whatever the model would have said.

The asymmetry is the whole design. The model can raise a turn to a crisis and
switch retrieval off; it can never talk the floor out of one. Being wrong in
the direction this allows costs a single skipped lookup, which nobody
notices. Being wrong the other way is unforgivable.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Phrases that mean the same thing in every reading of them.
#
# Kept deliberately short. Every entry earns its place by being something a
# person cannot plausibly say while fine — anything that merely *often* means
# distress belongs to the model's judgement, not here, because a floor that
# fires on ordinary sadness would switch off the memory of a system built to
# remember.
CRISIS_PHRASES: frozenset[str] = frozenset(
    {
        "kill myself",
        "killing myself",
        "end my life",
        "ending my life",
        "take my own life",
        "taking my own life",
        "want to die",
        "wanna die",
        "better off dead",
        "suicidal",
        "suicide",
        "hurt myself",
        "hurting myself",
        "harm myself",
        "harming myself",
        "cut myself",
        "cutting myself",
        "no reason to live",
        "nothing left to live for",
        "cant go on anymore",
        "can t go on anymore",
    }
)


def normalise(text: str) -> str:
    """
    A form of a sentence that small differences cannot hide behind.

    Capitalisation and punctuation go, and runs of whitespace collapse to one
    space. This is what makes "I can't go on anymore." and "i cant go on
    anymore" the same sentence — an apostrophe should not be the thing that
    decides whether the floor holds.
    """
    kept = "".join(char.casefold() if char.isalnum() else " " for char in text)
    return " ".join(kept.split())


def in_crisis(text: str) -> bool:
    """
    Whether this turn trips the floor.

    A plain substring test on the normalised sentence. Something cleverer —
    stemming, fuzzy distance, a classifier — would make this harder to read
    and harder to be sure of, and being sure of it is the only reason it
    exists.
    """
    if not text.strip():
        return False

    normalised = normalise(text)
    hit = next(
        (phrase for phrase in sorted(CRISIS_PHRASES) if phrase in normalised), None
    )
    if hit is None:
        return False

    # The phrase is logged, not the sentence. Knowing the floor fired is
    # operationally useful; writing somebody's worst moment into a log file
    # is not.
    logger.info("a turn tripped the distress floor", extra={"matched": hit})
    return True


__all__ = ["CRISIS_PHRASES", "in_crisis", "normalise"]
