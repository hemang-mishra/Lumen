"""
Removal of hesitation noises from spoken transcripts.

Only the sounds that can never mean anything are handled here — "um", "uh",
"hmm" and their spellings. Those are safe to delete by pattern because they
have no other use in English, so no amount of surrounding context could make
one of them meaningful.

Everything harder is deliberately left alone and passed to the language
model instead. "Like", "you know", "right" and "basically" all have real
senses — "right, so the issue was..." is someone organising their thoughts,
and "I like this" is a verb. Telling those apart needs an understanding of
the sentence, and a pattern cannot get it right often enough to be trusted
with someone's own words.

Applies to speech only. In typed text an "um" was typed on purpose.
"""

from __future__ import annotations

import re

# Hesitation sounds with no other meaning in English. Longer spellings come
# first so that "uh-huh" is matched whole instead of as "uh" plus leftovers.
STANDALONE_FILLERS: tuple[str, ...] = (
    "uh-huh",
    "mm-hmm",
    "uhh",
    "umm",
    "hmm",
    "mmm",
    "erm",
    "uh",
    "um",
    "er",
)

# A filler counts only as a whole word. The hyphen in the guards keeps "uh"
# from being torn out of "uh-huh", and the word guards keep "um" out of
# "umbrella".
_FILLER_RE = re.compile(
    r"(?<![\w-])(?:" + "|".join(STANDALONE_FILLERS) + r")(?![\w-])",
    re.IGNORECASE,
)

# Cleanups applied after removal, in order. Deleting a word from the middle
# of a sentence leaves debris — a double space, or a comma that has drifted
# away from the word it followed — and this puts it back together.
_TIDY_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[ \t]{2,}"), " "),                      # runs of spaces
    (re.compile(r"[ \t]+([,.!?;:])"), r"\1"),             # space before punctuation
    (re.compile(r"([,;:])(?:[ \t]*[,;:])+"), r"\1"),      # doubled separators
    (re.compile(r"^[ \t]*[,;:]+[ \t]*", re.MULTILINE), ""),  # a line starting on a comma
    (re.compile(r"[ \t]+\n"), "\n"),                     # trailing space on a line
)


def strip_standalone_fillers(text: str) -> tuple[str, int]:
    """
    Take the hesitation sounds out of a transcript.

    Returns the tidied text and how many sounds were removed, so a caller
    can log how much noise there was without keeping the text itself.

    The word count that decides how much attention an entry earns is taken
    after this runs, which is why the count matters: forty words of hesitant
    speech can be twenty-two real ones.
    """
    if not text:
        return text, 0

    stripped, removed = _FILLER_RE.subn("", text)
    if removed:
        stripped = _tidy(stripped)
    return stripped, removed


def _tidy(text: str) -> str:
    """Repair the spacing and punctuation left behind by a removal."""
    for pattern, replacement in _TIDY_RULES:
        text = pattern.sub(replacement, text)
    return text.strip()


__all__ = ["STANDALONE_FILLERS", "strip_standalone_fillers"]
