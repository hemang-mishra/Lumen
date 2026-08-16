"""
Turns that obviously need nothing looked up.

"Yeah." "Go on." "Thanks." A third of a conversation is this, and paying a
model to read each one is both a cost and a delay for an answer that was
never in doubt.

The rule is exact match against a fixed list, and nothing else. In
particular it is **not** a length rule, however tempting one looks: the
shortest turns in this kind of conversation are frequently the heaviest
ones, and anything that skipped four-word sentences would skip exactly the
ones that mattered most.
"""

from __future__ import annotations

from lumen.query.formulation.safety import normalise

# Turns that carry no subject of their own — they only acknowledge whatever
# was just said. Every entry here is a complete turn, matched whole; a
# sentence that merely starts with one of these words still goes to the
# model, because "right, so about my father" is not an acknowledgement.
TRIVIAL_TURNS: frozenset[str] = frozenset(
    {
        "yeah",
        "yep",
        "yes",
        "yup",
        "no",
        "nope",
        "ok",
        "okay",
        "k",
        "sure",
        "right",
        "got it",
        "i see",
        "makes sense",
        "that makes sense",
        "go on",
        "continue",
        "carry on",
        "thanks",
        "thank you",
        "thx",
        "ty",
        "cool",
        "nice",
        "great",
        "hmm",
        "hm",
        "mhm",
        "mm",
        "uh huh",
        "huh",
        "oh",
        "ah",
        "hi",
        "hey",
        "hello",
        "bye",
        "goodbye",
        "good night",
        "goodnight",
        "brb",
        "one sec",
        "wait",
        "interesting",
        "yeah interesting",
        "fair enough",
        "true",
        "exactly",
        "agreed",
    }
)


def is_trivial(text: str) -> bool:
    """
    Whether this turn is a plain acknowledgement and nothing more.

    Matched whole, on the normalised sentence, so punctuation and
    capitalisation make no difference and a longer sentence containing one of
    these words does not qualify.
    """
    return normalise(text) in TRIVIAL_TURNS


__all__ = ["TRIVIAL_TURNS", "is_trivial"]
