"""
How much of somebody's history to put in front of the assistant.

The original design said four hundred tokens and called it non-negotiable.
That number was chosen when the constraint was a three-second wait, and it
no longer is — so the cap here is not one number but four, and which one
applies is decided by how the person sounds.

The reason is not cost. It is that a wall of history in front of a light
question makes the answer worse: the assistant starts reaching for
connections nobody asked about instead of answering what was said. And in
the other direction, somebody thinking something through out loud can use
everything there is.

Crisis is the one that matters most, and it is zero. Not "a little" — none.
Somebody in acute distress needs the assistant fully present with them, not
consulting a file.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from lumen.config import ChatConfig
from lumen.schemas.enums import EmotionalRegister


@dataclass(frozen=True)
class Policy:
    """
    What one emotional register allows.

    Attributes:
        max_tokens: The ceiling on the whole briefing.
        max_records: How many separate pieces of history may appear.
        allow_quotes: Whether the person's own words may be repeated back.
            Off when they are raw: hearing your own sentence quoted at you
            mid-breakdown lands as being studied rather than heard.
        standing_only: Whether to offer only the settled records — the
            patterns and beliefs — rather than individual notes. Also for the
            raw case, where the useful thing is the shape of a recurring
            problem, not a transcript of the last time it happened.
    """

    max_tokens: int
    max_records: int
    allow_quotes: bool = True
    standing_only: bool = False

    @property
    def injects_anything(self) -> bool:
        """Whether this register allows a briefing at all."""
        return self.max_tokens > 0 and self.max_records > 0


# Records that are settled conclusions rather than single moments. What the
# raw case is limited to.
STANDING_KINDS: frozenset[str] = frozenset(
    {"PatternNode", "BeliefNode", "LessonNode", "AdoptedPrincipleNode"}
)


def policies(config: ChatConfig | None = None) -> dict[EmotionalRegister, Policy]:
    """
    The allowance for each way somebody can sound.

    Built from configuration rather than fixed, because the right numbers are
    only discoverable by reading real briefings and deciding whether they
    helped.
    """
    settings = config or ChatConfig()
    return {
        EmotionalRegister.CRISIS: Policy(
            max_tokens=0, max_records=0, allow_quotes=False
        ),
        EmotionalRegister.VULNERABLE: Policy(
            max_tokens=settings.vulnerable_tokens,
            max_records=settings.vulnerable_records,
            allow_quotes=False,
            standing_only=True,
        ),
        EmotionalRegister.STABLE: Policy(
            max_tokens=settings.stable_tokens,
            max_records=settings.stable_records,
        ),
        EmotionalRegister.REFLECTIVE: Policy(
            max_tokens=settings.reflective_tokens,
            max_records=settings.reflective_records,
        ),
    }


def policy_for(
    register: EmotionalRegister, config: ChatConfig | None = None
) -> Policy:
    """The allowance for how the person sounds right now."""
    return policies(config)[register]


def estimate_tokens(text: str, *, chars_per_token: float = 4.0) -> int:
    """
    Roughly how much of a model's attention a piece of text will take.

    Estimated from length rather than counted with a real tokeniser, and
    that is a deliberate trade. A tokeniser belongs to one model — swapping
    the assistant would silently change every budget in the system — and the
    number here only has to be close enough to stop a briefing running long.

    Rounded up, so the estimate errs towards saying something is bigger than
    it is. Being wrong in that direction costs a sentence; being wrong the
    other way costs a truncated prompt.
    """
    if not text:
        return 0
    return math.ceil(len(text) / max(chars_per_token, 1.0))


__all__ = [
    "Policy",
    "STANDING_KINDS",
    "policies",
    "policy_for",
    "estimate_tokens",
]
