"""
Saving one finished report, all of it or none of it.

This is the only place in the package that writes, and it makes no decisions
while doing so. Everything worth arguing about was settled before it was
called; what is left is to put the document down and join it to the writing it
covers.

The joining is not decoration. A report says it read fourteen pieces of
writing, and without the links that is a claim nobody can check. With them,
anyone reading a single entry months later can see which summaries drew on it
— which is also how a report is unpicked if it turns out to be wrong.

The whole thing goes down inside one transaction. A report that exists but is
joined to half of its evidence would be indistinguishable from one that
genuinely only read half.
"""

from __future__ import annotations

import logging

from lumen.graph.provider import GraphProvider
from lumen.schemas.nodes import MacroextractionReportNode

logger = logging.getLogger(__name__)

# The link that records which writing a report drew on.
COVERAGE_EDGE = "analyzed_in"


def write(
    report: MacroextractionReportNode,
    episode_ids: tuple[str, ...],
    *,
    graph: GraphProvider,
) -> str:
    """
    Store one report and join it to everything it read.

    Returns the identifier it was stored under. The report is not added to
    the search index, and that is deliberate rather than an omission: a
    report is *about* somebody's history, and letting it come back as a
    search result would let the system quote its own summary back to the
    person as though they had said it.
    """
    with graph.transaction():
        node_id = graph.write_node("MacroextractionReportNode", report)

        for episode_id in dict.fromkeys(episode_ids):
            graph.write_edge(
                COVERAGE_EDGE,
                episode_id,
                node_id,
                {"valid_from": report.created_at.isoformat()},
            )

    logger.info(
        "wrote a periodic report",
        extra={
            "report_id": node_id,
            "report_type": report.report_type.value,
            "period_start": report.period_start.isoformat(),
            "episodes_analyzed": report.episodes_analyzed,
            "archetype_shift_detected": report.archetype_shift_detected,
        },
    )
    return node_id


def count_existing(
    graph: GraphProvider, *, report_type: str, period_start
) -> int:
    """
    How many reports already cover this exact period.

    Asked before writing, for two different reasons at once. It is how an
    ordinary run knows to skip a period that has been covered, and it is how
    a deliberate re-run picks a name that does not collide with what is
    already there.
    """
    return len(
        graph.find_reports(report_type=report_type, period_start=period_start, limit=50)
    )


__all__ = ["COVERAGE_EDGE", "write", "count_existing"]
