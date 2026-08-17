"""
Choosing which pieces of history actually go in.

Retrieval hands over a dozen records. A briefing holds two to six. This is
where that gets decided, and the interesting part is not the ranking — that
already happened — but the three rules that override it.

**The same insight twice is worse than two insights.** Two records that read
almost alike are one piece of information taking up two slots, and a strong
theme will happily produce four of them. The second one is dropped.

**No more than a few of any one kind.** Six patterns and nothing else is a
worse briefing than three patterns, a belief and an open question, even when
the six all rank higher. Variety is what makes the assistant able to see a
connection rather than a repetition.

**When somebody is raw, only settled records.** A pattern they have had for
years can be spoken about gently. A note from the worst evening of last month
cannot, and the useful thing at that moment is the shape of the problem
rather than a transcript of the last time it happened.

Everything dropped is recorded with the rule that dropped it, because a
briefing that disappoints is almost always explained by what is missing.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from lumen.config import ChatConfig
from lumen.query.assembly.budget import STANDING_KINDS, Policy, estimate_tokens
from lumen.query.assembly.contracts import ContextItem, DroppedItem
from lumen.query.retrieval.contracts import RetrievedNode

logger = logging.getLogger(__name__)

# Why something did not make it, in the words the record uses.
OVER_BUDGET = "over_budget"
OVER_COUNT = "over_count"
DUPLICATE = "duplicate"
TOO_MANY_OF_A_KIND = "too_many_of_a_kind"
NOT_SETTLED_ENOUGH = "not_settled_enough"


def choose(
    rendered: Iterable[tuple[RetrievedNode, str]],
    *,
    policy: Policy,
    config: ChatConfig,
) -> tuple[list[ContextItem], list[DroppedItem]]:
    """
    Take the briefing lines that fit, in order, and say what was left out.

    Works through the ranked list once. Nothing is reordered here: retrieval
    decided what matters most and this only decides where to stop.
    """
    kept: list[ContextItem] = []
    dropped: list[DroppedItem] = []
    # What the chosen records actually say, which is what a repeat is
    # measured against. Comparing the finished lines instead would count the
    # wording every briefing of a kind shares — "Pattern:", "Seen 3 times" —
    # and make two unrelated patterns look like the same one.
    said: list[str] = []
    per_kind: dict[str, int] = {}
    spent = 0

    for node, text in rendered:
        reason = _refusal(
            node,
            text,
            said=said,
            kept=kept,
            per_kind=per_kind,
            spent=spent,
            policy=policy,
            config=config,
        )
        if reason is not None:
            dropped.append(DroppedItem(node_id=node.node_id, reason=reason))
            continue

        cost = estimate_tokens(text, chars_per_token=config.chars_per_token)
        kept.append(
            ContextItem(
                node_id=node.node_id,
                node_type=node.node_type,
                text=text,
                tokens=cost,
                score=node.rank_score,
                found_by=node.found_by,
                boosted=node.boosted,
            )
        )
        per_kind[node.node_type] = per_kind.get(node.node_type, 0) + 1
        said.append(node.preview)
        spent += cost

    if dropped:
        logger.debug(
            "some of what was fetched did not fit the briefing",
            extra={"kept": len(kept), "dropped": len(dropped), "tokens": spent},
        )
    return kept, dropped


def _refusal(
    node: RetrievedNode,
    text: str,
    *,
    said: list[str],
    kept: list[ContextItem],
    per_kind: dict[str, int],
    spent: int,
    policy: Policy,
    config: ChatConfig,
) -> str | None:
    """
    Why this line cannot go in, or nothing if it can.

    The order is deliberate: the cheapest and most absolute checks first, so
    a record refused on principle is never measured or compared against
    anything.
    """
    if policy.standing_only and node.node_type not in STANDING_KINDS:
        return NOT_SETTLED_ENOUGH
    if len(kept) >= policy.max_records:
        return OVER_COUNT
    if per_kind.get(node.node_type, 0) >= config.per_kind_cap:
        return TOO_MANY_OF_A_KIND
    if _too_alike(node.preview, said, config.duplicate_threshold):
        return DUPLICATE

    cost = estimate_tokens(text, chars_per_token=config.chars_per_token)
    if spent + cost > policy.max_tokens:
        return OVER_BUDGET
    return None


def _too_alike(preview: str, said: list[str], threshold: float) -> bool:
    """Whether this record says what one already chosen has said."""
    return any(overlap(preview, chosen) >= threshold for chosen in said)


def overlap(left: str, right: str) -> float:
    """
    How much two sentences have in common, from 0 to 1.

    Measured on the words they share rather than on meaning, which is crude
    and is the right amount of machinery for the job: this only has to catch
    two briefings that are visibly the same thing, and anything cleverer
    would be a second ranking system to keep honest.

    Compared against the smaller of the two, so a short line that is entirely
    contained in a longer one counts as a repeat — which is exactly the case
    worth catching.
    """
    first = _words(left)
    second = _words(right)
    if not first or not second:
        return 0.0
    shared = len(first & second)
    return shared / min(len(first), len(second))


def _words(text: str) -> set[str]:
    """The meaningful words of a sentence, for comparing two of them."""
    cleaned = "".join(
        char.casefold() if char.isalnum() or char.isspace() else " " for char in text
    )
    return {word for word in cleaned.split() if word not in _IGNORED}


# Words that two unrelated sentences share anyway, and which would otherwise
# make every briefing look like a repeat of every other.
_IGNORED = frozenset(
    {
        "a", "an", "and", "the", "of", "to", "in", "on", "at", "it", "is", "was",
        "they", "them", "their", "he", "she", "his", "her", "i", "me", "my",
        "that", "this", "with", "for", "from", "as", "but", "or", "so", "then",
    }
)


__all__ = [
    "choose",
    "overlap",
    "OVER_BUDGET",
    "OVER_COUNT",
    "DUPLICATE",
    "TOO_MANY_OF_A_KIND",
    "NOT_SETTLED_ENOUGH",
]
