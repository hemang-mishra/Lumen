"""
Spotting the difference between several things changing and one thing changing.

Individual patterns move all the time. Someone has a quiet month, or a hard
one, and half their usual habits shift in frequency without anything deeper
having happened. What this looks for is narrower: enough separate patterns
moving the *same* way, across a long enough stretch, that the plainest reading
is that the person's frame of reference moved rather than their circumstances.

The whole of the detection is counting. How many patterns trend consistently,
against a threshold. Naming the shift — deciding it is "approval-seeking
giving way to internal reference" rather than a list of five patterns — is a
judgement, and that is asked of the model, only after this has said there is
something to name.

One trend is worth explaining. A pattern that fires exactly as often as it did
but is now caught in the act more often has changed, and counting only how
often it fired would score that as no change at all.
"""

from __future__ import annotations

import logging

from lumen.config import MacroConfig
from lumen.pipeline.macroextraction import windows
from lumen.pipeline.macroextraction.contracts import (
    ArchetypeShiftFacts,
    TrendingPattern,
    WindowCorpus,
)
from lumen.schemas.enums import PatternTrend, ReportType

logger = logging.getLogger(__name__)

# The kinds of report long enough for the comparison to say anything. A week
# held against the previous quarter mostly measures the difference in length.
COMPARABLE_TYPES: frozenset[ReportType] = frozenset(
    {ReportType.MONTHLY, ReportType.QUARTERLY}
)

# Which trends point the same way. A shift is several patterns moving in one
# direction, and "the person is loosening their grip on this" covers both a
# habit fading and a habit being caught in the act more often.
_LOOSENING: frozenset[PatternTrend] = frozenset(
    {PatternTrend.FREQUENCY_DECREASING, PatternTrend.AWARENESS_INCREASING}
)
_TIGHTENING: frozenset[PatternTrend] = frozenset({PatternTrend.FREQUENCY_INCREASING})


def detect_shift(
    corpus: WindowCorpus,
    pattern_episodes: dict[str, set[str]],
    *,
    config: MacroConfig,
) -> ArchetypeShiftFacts:
    """
    Whether enough patterns moved together to call it a shift.

    Answers "not detected" for the short kinds of report rather than
    pretending to have looked. A weekly report that quietly always says no
    reads the same as one that checked and found nothing, and only one of
    those is true.
    """
    if corpus.window.report_type not in COMPARABLE_TYPES:
        return ArchetypeShiftFacts()
    if not corpus.comparison_counts and not pattern_episodes:
        return ArchetypeShiftFacts()

    comparison = windows.comparison_window(corpus.window, config=config)
    trends = _trends(corpus, pattern_episodes)

    loosening = [item for item in trends if item.trend in _LOOSENING]
    tightening = [item for item in trends if item.trend in _TIGHTENING]

    threshold = max(config.archetype_min_patterns, 2)
    leading = loosening if len(loosening) >= len(tightening) else tightening
    detected = len(leading) >= threshold

    if detected:
        logger.info(
            "several patterns moved the same way across a long stretch",
            extra={
                "period_start": corpus.window.period_start.isoformat(),
                "report_type": corpus.window.report_type.value,
                "patterns": len(leading),
            },
        )

    return ArchetypeShiftFacts(
        detected=detected,
        contributing_patterns=tuple(leading if detected else trends),
        comparison_start=comparison.period_start,
        comparison_end=comparison.period_end,
    )


def _trends(
    corpus: WindowCorpus, pattern_episodes: dict[str, set[str]]
) -> tuple[TrendingPattern, ...]:
    """
    Which way each pattern moved between the two stretches.

    Every pattern seen in either stretch is considered, not only those seen in
    both. A pattern that appeared for the first time and one that stopped
    entirely are the two clearest movements there are, and requiring a
    presence on both sides would discard exactly those.
    """
    considered = set(pattern_episodes) | set(corpus.comparison_counts)
    trends: list[TrendingPattern] = []

    for pattern_id in sorted(considered):
        recent = len(pattern_episodes.get(pattern_id, ()))
        earlier = corpus.comparison_counts.get(pattern_id, 0)
        trend = _classify(
            recent=recent,
            earlier=earlier,
            recent_awareness=corpus.awareness_counts.get(pattern_id, 0),
            earlier_awareness=corpus.previous_awareness_counts.get(pattern_id, 0),
        )
        if trend is PatternTrend.STEADY:
            continue
        trends.append(
            TrendingPattern(
                pattern_id=pattern_id,
                label=_label_for(corpus, pattern_id),
                trend=trend,
                recent_count=recent,
                earlier_count=earlier,
            )
        )

    return tuple(trends)


def _classify(
    *, recent: int, earlier: int, recent_awareness: int, earlier_awareness: int
) -> PatternTrend:
    """
    One pattern's direction of travel.

    Growing awareness is checked first and only where the frequency held
    steady. A habit that is both fading and being caught more often is
    already fading, and reporting it twice under two headings would let one
    pattern count towards a threshold on its own.
    """
    if recent == earlier and recent_awareness > earlier_awareness:
        return PatternTrend.AWARENESS_INCREASING
    if recent > earlier:
        return PatternTrend.FREQUENCY_INCREASING
    if recent < earlier:
        return PatternTrend.FREQUENCY_DECREASING
    return PatternTrend.STEADY


def _label_for(corpus: WindowCorpus, pattern_id: str) -> str:
    """What a pattern is called, wherever its record happens to have been read."""
    record = corpus.patterns.get(pattern_id)
    if record is None:
        record = next(
            (
                row
                for row in corpus.all_patterns
                if str(row.get("node_id")) == pattern_id
            ),
            None,
        )
    if record is None:
        return pattern_id
    return str(record.get("pattern_name") or pattern_id)


__all__ = ["COMPARABLE_TYPES", "detect_shift"]
