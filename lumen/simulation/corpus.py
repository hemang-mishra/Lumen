"""
Five consecutive days of journal entries, and what should become of them.

Written as a week someone might actually have had: one running thread about
comparing himself to a colleague, told differently each day, plus enough
unrelated material that the thread has to be recognised rather than being
the only thing present.

Each day carries three things beyond its text. The themes it is about, which
is what lets the stand-in embedder place it near the other days about the
same thing. The replies each model step should give, because the models here
are stand-ins and something has to speak for them. And a plain-English
statement of what the day is *for* — written before the replies were, and
left in place so that a day quietly reworded to make a test pass shows up as
a day whose intent no longer matches its content.

The arc, in one line each:

  1. Something is noticed for the first time.
  2. It happens again, differently, with the same person involved.
  3. It happens again with nobody named and none of the same words.
  4. He understands it differently, and the belief changes.
  5. Two unrelated things in one sitting, one of which argues with day four.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date

from lumen.simulation.themes import Theme

# ---------------------------------------------------------------------------
# What the entries are about
# ---------------------------------------------------------------------------

COMPARISON = Theme(
    name="comparison",
    keywords=(
        "comparing",
        "compare",
        "compared",
        "behind",
        "ahead of me",
        "measuring myself",
        "everyone else",
        "further along",
    ),
)

SOLITUDE = Theme(
    name="solitude",
    keywords=("alone", "on my own", "by myself", "quiet", "solitude"),
)

COOKING = Theme(name="cooking", keywords=("cooked", "kitchen", "dinner", "cafe"))

THEMES: tuple[Theme, ...] = (COMPARISON, SOLITUDE, COOKING)


# ---------------------------------------------------------------------------
# The shape of one day
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DayExpectation:
    """
    What a day should leave behind, checked as soon as it has run.

    Checked per day rather than only at the end. A run where day two
    quietly created a second pattern still ends with a graph that has
    patterns in it, and noticing on day five means working backwards
    through four days to find where it went wrong.

    Attributes:
        new_patterns: How many standing patterns this day should create.
        new_beliefs: How many standing beliefs this day should create.
        reinforced: Records whose evidence count this day should move.
        evolves: Records this day should replace with a newer version.
        episodes: How many separate topics the day should split into.
        escalations: How many items should be left for the person.
    """

    new_patterns: int = 0
    new_beliefs: int = 0
    reinforced: tuple[str, ...] = ()
    evolves: tuple[str, ...] = ()
    episodes: int = 1
    escalations: int = 0


@dataclass(frozen=True)
class SimulatedDay:
    """
    One day's entry, and everything needed to process it the same way twice.

    Attributes:
        day: Which day of the run this is, counting from one.
        event_date: The calendar day it belongs to.
        text: What the person wrote.
        themes: What it is about, which is what places it for the embedder.
        intent: What this day is for, in one sentence. Written before the
            replies were, and the thing to check against if a day starts
            passing for the wrong reason.
        replies: What each model step should answer, keyed by step name.
        expects: What should be true once this day has been saved.
    """

    day: int
    event_date: date
    text: str
    themes: tuple[str, ...]
    intent: str
    replies: dict[str, str] = field(default_factory=dict)
    expects: DayExpectation = field(default_factory=DayExpectation)


# ---------------------------------------------------------------------------
# Reply builders
#
# The shapes each stage expects, written once. A day supplies what is
# different about it and nothing else, so a day's entry in the corpus reads
# as a journal entry rather than as four screens of JSON.
# ---------------------------------------------------------------------------


def _cleaned(text: str) -> str:
    """The cleaning step, which for typed English has nothing to do."""
    return json.dumps(
        {"cleaned_text": text, "detected_languages": ["en"], "translated": False}
    )


def _one_episode(text: str, summary: str, themes: list[str], people: list[str]) -> str:
    """Splitting a day that is about one thing."""
    return json.dumps(
        {
            "episodes": [
                {
                    "episode_summary": summary,
                    "text": text,
                    "overarching_themes": themes,
                }
            ],
            "coreference": {
                "resolved_entities": [
                    {
                        "span": "he",
                        "resolved_to": name,
                        "confidence": 0.9,
                        "resolution_basis": "the only person named in the entry",
                    }
                    for name in people
                ],
                "ambiguous_refs": [],
            },
        }
    )


def _two_episodes(first: dict, second: dict) -> str:
    """Splitting a day that moved between two unrelated subjects."""
    return json.dumps(
        {
            "episodes": [
                {
                    "episode_summary": piece["summary"],
                    "text": piece["text"],
                    "overarching_themes": piece["themes"],
                }
                for piece in (first, second)
            ],
            "coreference": {"resolved_entities": [], "ambiguous_refs": []},
        }
    )


def _scored(count: int, score: float = 0.85) -> str:
    """Judging each piece worth reading closely."""
    return json.dumps(
        {
            "scores": [
                {
                    "episode_index": index,
                    "coherence_score": score,
                    "reason": "a clear thought with a feeling and a context",
                }
                for index in range(1, count + 1)
            ]
        }
    )


def _found(
    observations: list[dict], events: list[dict] | None = None
) -> str:
    """What was read out of an entry."""
    return json.dumps(
        {
            "observations": [
                {
                    "type": item.get("type", "PATTERN"),
                    "content": item["content"],
                    "raw_evidence": [item["quote"]],
                    "extraction_signal_strength": item.get("signal", "HIGH"),
                    "person_ref": item.get("person"),
                }
                for item in observations
            ],
            "events": [
                {
                    "event_summary": item["summary"],
                    "raw_evidence": [item["quote"]],
                    "person_refs": item.get("people", []),
                }
                for item in (events or [])
            ],
            "causal_mechanisms": [],
        }
    )


def _searches_for(*texts: str) -> str:
    """The made-up historical record used to search with, one per finding."""
    return json.dumps(
        {
            "hypotheticals": [
                {"index": index, "text": text}
                for index, text in enumerate(texts, start=1)
            ]
        }
    )


def _decided(items: list[dict], people: list[dict] | None = None) -> str:
    """What each finding means for what came before it."""
    return json.dumps(
        {
            "decisions": [
                {
                    "item_index": index,
                    "primary": {
                        "action": item["action"],
                        "target_node_id": item.get("target"),
                        "confidence": item.get("confidence", 0.94),
                        "reason": item.get("reason", "matches what came before"),
                    },
                    "runner_up": {
                        "action": item.get("runner_up", "AMBIGUOUS"),
                        "confidence": item.get("runner_up_confidence", 0.1),
                    },
                    **(
                        {"new_node": item["new_node"]} if item.get("new_node") else {}
                    ),
                    **(
                        {"delta_description": item["delta"]} if item.get("delta") else {}
                    ),
                    **(
                        {"contradiction_summary": item["clash"]}
                        if item.get("clash")
                        else {}
                    ),
                }
                for index, item in enumerate(items, start=1)
            ],
            "people": people or [],
        }
    )


def _confirmed(items: list[dict]) -> str:
    """The careful model's verdict on anything consequential."""
    return json.dumps(
        {
            "verdicts": [
                {
                    "item_index": item["index"],
                    "confirmed": True,
                    "primary": {
                        "action": item["action"],
                        "target_node_id": item.get("target"),
                        "confidence": item.get("confidence", 0.95),
                        "reason": item.get("reason", "the change is real"),
                    },
                    **(
                        {"delta_description": item["delta"]} if item.get("delta") else {}
                    ),
                    **(
                        {"contradiction_summary": item["clash"]}
                        if item.get("clash")
                        else {}
                    ),
                }
                for item in items
            ]
        }
    )


# ---------------------------------------------------------------------------
# The five days
# ---------------------------------------------------------------------------

# What the standing records are called once created. Named here rather than
# guessed at in the replies, so a day that reinforces yesterday's pattern is
# visibly pointing at the same thing.
PATTERN_COMPARISON = "pat_comparison_spiral"
BELIEF_PACE = "bel_pace_not_ability"

DAY_1 = SimulatedDay(
    day=1,
    event_date=date(2026, 3, 2),
    text=(
        "I went to the cafe alone today and ate there without the usual dread. "
        "Then I saw what Alex had shipped this week and felt small and behind. "
        "I sat with it for a while and the pressure lifted on its own. "
        "I think the comparing is the thing that hurts, not the gap itself."
    ),
    themes=("comparison", "cooking", "solitude"),
    intent="The comparison pattern is noticed for the first time and becomes a standing record.",
    replies={
        "extract_reflection": _found(
            [
                {
                    "content": "Comparing himself to Alex is what causes the pain, not the gap",
                    "quote": "I think the comparing is the thing that hurts",
                    "person": "Alex",
                }
            ],
            [
                {
                    "summary": "Ate at the cafe alone without the usual dread",
                    "quote": "I went to the cafe alone today",
                }
            ],
        ),
        "hyde": _searches_for(
            "He compares himself to other people and it hurts",
            "He ate somewhere on his own",
            "He reflected on comparing himself to others",
        ),
        "decision": _decided(
            [
                {
                    "action": "BRANCH",
                    "confidence": 0.9,
                    "reason": "nothing like this has been recorded before",
                    "new_node": {
                        "kind": "PATTERN",
                        "name": "comparison spiral",
                        "statement": "Comparing himself to others is what hurts, not the gap",
                        "domain": "SELF_CONCEPT",
                    },
                },
                {"action": "AMBIGUOUS", "confidence": 0.2, "reason": "an ordinary meal"},
                {"action": "AMBIGUOUS", "confidence": 0.2, "reason": "the session itself"},
            ],
            people=[{"name": "Alex", "relationship": "COLLEAGUE", "sentiment": "MIXED"}],
        ),
    },
    expects=DayExpectation(new_patterns=1, escalations=2),
)

DAY_2 = SimulatedDay(
    day=2,
    event_date=date(2026, 3, 3),
    text=(
        "Alex mentioned his promotion in standup and something in my chest dropped. "
        "Different situation, exactly the same feeling as yesterday. "
        "I am measuring myself against him again and it is making me miserable."
    ),
    themes=("comparison",),
    intent=(
        "The same pattern happens again with the same person named. It should gain "
        "evidence rather than create a second pattern."
    ),
    replies={
        "extract_reflection": _found(
            [
                {
                    "content": "Measuring himself against Alex again, and it makes him miserable",
                    "quote": "I am measuring myself against him again",
                    "person": "Alex",
                }
            ]
        ),
        "hyde": _searches_for(
            "He compares himself to other people and it hurts",
            "He reflected on comparing himself to others",
        ),
        "decision": _decided(
            [
                {
                    "action": "REINFORCE",
                    "target": PATTERN_COMPARISON,
                    "confidence": 0.93,
                    "reason": "the same pattern, a second time",
                },
                {"action": "AMBIGUOUS", "confidence": 0.2, "reason": "the session itself"},
            ],
            people=[{"name": "Alex", "relationship": "COLLEAGUE", "sentiment": "MIXED"}],
        ),
    },
    expects=DayExpectation(reinforced=(PATTERN_COMPARISON,), escalations=1),
)

DAY_3 = SimulatedDay(
    day=3,
    event_date=date(2026, 3, 4),
    text=(
        "Everyone else seems further along and I cannot tell if that is true or "
        "just how it looks from here. Nobody in particular set this off today. "
        "It is just there, a low hum of being behind."
    ),
    themes=("comparison",),
    intent=(
        "The same theme with nobody named and none of yesterday's words. It should "
        "still find the pattern — recall must not depend on wording or on a person."
    ),
    replies={
        "extract_reflection": _found(
            [
                {
                    "content": "A constant low sense of being further behind than everyone else",
                    "quote": "a low hum of being behind",
                }
            ]
        ),
        "hyde": _searches_for(
            "He compares himself to other people and it hurts",
            "He reflected on comparing himself to others",
        ),
        "decision": _decided(
            [
                {
                    "action": "REINFORCE",
                    "target": PATTERN_COMPARISON,
                    "confidence": 0.91,
                    "reason": "the same pattern again, without a trigger",
                },
                {"action": "AMBIGUOUS", "confidence": 0.2, "reason": "the session itself"},
            ]
        ),
    },
    expects=DayExpectation(reinforced=(PATTERN_COMPARISON,), escalations=1),
)

DAY_4 = SimulatedDay(
    day=4,
    event_date=date(2026, 3, 5),
    text=(
        "Talking it through I realised what I am actually comparing is pace, not "
        "ability. I am not worse at this than Alex. I started three years later. "
        "That reframing took the sting out of it more than anything else has."
    ),
    themes=("comparison",),
    intent=(
        "He understands the same thing differently. A belief is created and the "
        "understanding changes, which should produce a version chain rather than "
        "overwriting anything."
    ),
    replies={
        "extract_reflection": _found(
            [
                {
                    # A reframe rather than a pattern, because what a finding
                    # becomes is decided by its type and not by what the
                    # deciding model asks for. A reframe becomes a belief.
                    "type": "CONCEPTUAL_REFRAME",
                    "content": "What he compares is pace, not ability; he started three years later",
                    "quote": "what I am actually comparing is pace, not ability",
                    "person": "Alex",
                }
            ]
        ),
        "hyde": _searches_for(
            "He believes he is behind because he is worse at this",
            "He reflected on what the comparison is really about",
        ),
        "decision": _decided(
            [
                {
                    "action": "BRANCH",
                    "confidence": 0.92,
                    "reason": "a new understanding, stated for the first time",
                    "new_node": {
                        "kind": "BELIEF",
                        "name": "pace not ability",
                        "statement": "What I compare is pace, not ability — I started later",
                        "domain": "SELF_CONCEPT",
                    },
                },
                {"action": "AMBIGUOUS", "confidence": 0.2, "reason": "the session itself"},
            ],
            people=[{"name": "Alex", "relationship": "COLLEAGUE", "sentiment": "MIXED"}],
        ),
    },
    expects=DayExpectation(new_beliefs=1, escalations=1),
)

DAY_5 = SimulatedDay(
    day=5,
    event_date=date(2026, 3, 6),
    text=(
        "The comparing is not just about pace. I avoid the work that would close "
        "the gap, and then I read the gap as proof I am behind. It is not that I "
        "started later, it is what I do about it. "
        "Separately: I cooked a proper dinner for the first time in weeks and the "
        "kitchen felt like mine again rather than a place I pass through."
    ),
    themes=("comparison", "cooking"),
    intent=(
        "Two unrelated subjects in one sitting. The first deepens Thursday's "
        "understanding, which should replace it with a newer version rather than "
        "overwriting or duplicating it; the second is unrelated. Their episodes "
        "should be ordered."
    ),
    replies={
        "structure": _two_episodes(
            {
                "summary": "Realising the gap is avoidance, not just a late start",
                "text": (
                    "The comparing is not just about pace. I avoid the work that "
                    "would close the gap, and then I read the gap as proof I am "
                    "behind. It is not that I started later, it is what I do about it."
                ),
                "themes": ["comparison"],
            },
            {
                "summary": "Cooking a proper dinner and the kitchen feeling like his",
                "text": (
                    "I cooked a proper dinner for the first time in weeks and the "
                    "kitchen felt like mine again rather than a place I pass through."
                ),
                "themes": ["cooking"],
            },
        ),
        "triage": _scored(2),
        "extract_reflection": _found(
            [
                {
                    "type": "CONCEPTUAL_REFRAME",
                    "content": (
                        "The gap is what he avoids doing about it, not when he started"
                    ),
                    "quote": "it is what I do about it",
                }
            ]
        ),
        "hyde": _searches_for(
            "He believes the gap is about pace rather than ability",
            "He reflected on what the comparison is really about",
        ),
        "decision": _decided(
            [
                {
                    "action": "EVOLVE",
                    "target": BELIEF_PACE,
                    "confidence": 0.95,
                    "reason": "the same belief, understood one step further",
                    "delta": (
                        "Widened from 'I started later' to 'I avoid the work that "
                        "would close the gap, and then read the gap as proof'."
                    ),
                },
                {"action": "AMBIGUOUS", "confidence": 0.2, "reason": "the session itself"},
            ]
        ),
        "escalation": _confirmed(
            [
                {
                    "index": 1,
                    "action": "EVOLVE",
                    "target": BELIEF_PACE,
                    "confidence": 0.95,
                    "reason": "the change is real and he stated it himself",
                    "delta": (
                        "Widened from 'I started later' to 'I avoid the work that "
                        "would close the gap, and then read the gap as proof'."
                    ),
                }
            ]
        ),
    },
    expects=DayExpectation(episodes=2, evolves=(BELIEF_PACE,), escalations=2),
)

CORPUS: tuple[SimulatedDay, ...] = (DAY_1, DAY_2, DAY_3, DAY_4, DAY_5)


def replies_for(day: SimulatedDay) -> dict[str, str]:
    """
    Everything a day should answer, including the steps it never varies.

    The cleaning, splitting and scoring steps are the same for a day that
    does nothing unusual with them, so a day only writes them down when it
    differs — which for four of the five days means not at all.
    """
    return {
        "normalize_text": _cleaned(day.text),
        "structure": _one_episode(
            day.text,
            summary=day.intent,
            themes=list(day.themes),
            people=["Alex"] if "Alex" in day.text else [],
        ),
        "triage": _scored(1),
        "extract_raw_capture": json.dumps({"context": day.text[:80], "emotion": None}),
        **day.replies,
    }


__all__ = [
    "THEMES",
    "COMPARISON",
    "SOLITUDE",
    "COOKING",
    "CORPUS",
    "DAY_1",
    "DAY_2",
    "DAY_3",
    "DAY_4",
    "DAY_5",
    "PATTERN_COMPARISON",
    "BELIEF_PACE",
    "SimulatedDay",
    "DayExpectation",
    "replies_for",
]
