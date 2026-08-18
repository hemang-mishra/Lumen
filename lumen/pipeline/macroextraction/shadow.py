"""
Noticing that something is shifting while it is still shifting.

Everything else in this package looks back over a closed period. This looks at
the last two days, and it exists because the most useful moment to know that
somebody's thinking is moving is while it is moving, not in a summary written
three weeks later.

What it watches is not the writing but the decisions made about it. A burst of
new things branching off, or of tensions being recorded, is the shape a shift
takes in the record — several separate parts of what a person holds being
revised inside a couple of days.

Two conditions have to hold together, and the second is the one that does the
work. Enough decisions, and enough *separate things* affected. One belief being
turned over repeatedly across a hard evening is a person working something
through, not a shift, and counting decisions alone would call it one.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from lumen.config import MacroConfig
from lumen.graph.provider import ReadOnlyGraph
from lumen.graph.queries import tidy_row
from lumen.pipeline.macroextraction.contracts import MacroWindow, ShadowFinding
from lumen.schemas.enums import DecisionStatus, ReconciliationAction

logger = logging.getLogger(__name__)

# The two decisions that mean something changed direction rather than simply
# being confirmed again. Reinforcing what was already there is the opposite of
# what this looks for.
SHIFT_ACTIONS: frozenset[str] = frozenset(
    {ReconciliationAction.BRANCH.value, ReconciliationAction.CONTRADICT.value}
)

# Decisions that never actually took effect. A decision waiting for the
# person, or held back for being too uncertain, has changed nothing yet.
INERT_STATUSES: frozenset[str] = frozenset(
    {
        DecisionStatus.ROLLED_BACK.value,
        DecisionStatus.PENDING_HITL.value,
        DecisionStatus.BELOW_THRESHOLD.value,
        DecisionStatus.SUSPENDED_QUEUE_FULL.value,
        DecisionStatus.EXTRACTION_FAILED.value,
    }
)


def scan(
    window: MacroWindow, *, graph: ReadOnlyGraph, config: MacroConfig
) -> tuple[ShadowFinding, list[dict[str, Any]]]:
    """
    Whether the last couple of days hold a burst worth surfacing.

    Returns both the verdict and the decisions behind it, so the caller can
    describe what was found without reading the graph a second time.

    Finding nothing is the ordinary outcome and is reported as such. Writing a
    note every day saying nothing shifted would bury the days when something
    did, which is the entire value of the scan.
    """
    decisions = _recent_shift_decisions(window, graph=graph, config=config)
    if not decisions:
        return ShadowFinding(), []

    targets = {
        str(row.get("target_node_id") or row.get("source_node_id") or "")
        for row in decisions
    } - {""}

    enough_decisions = len(decisions) >= max(config.shadow_min_decisions, 1)
    enough_targets = len(targets) >= max(config.shadow_min_targets, 1)
    detected = enough_decisions and enough_targets

    finding = ShadowFinding(
        detected=detected,
        trigger_nodes=tuple(sorted(str(row.get("node_id")) for row in decisions)),
        episode_ids=_episodes_behind(decisions, graph=graph),
        branch_count=sum(
            1
            for row in decisions
            if str(row.get("action")) == ReconciliationAction.BRANCH.value
        ),
        contradict_count=sum(
            1
            for row in decisions
            if str(row.get("action")) == ReconciliationAction.CONTRADICT.value
        ),
        target_count=len(targets),
    )

    if detected:
        logger.info(
            "several things shifted in the last couple of days",
            extra={
                "decisions": len(decisions),
                "targets": len(targets),
                "hours": config.shadow_window_hours,
            },
        )
    else:
        logger.debug(
            "recent decisions did not add up to a shift",
            extra={"decisions": len(decisions), "targets": len(targets)},
        )

    return finding, decisions


def _recent_shift_decisions(
    window: MacroWindow, *, graph: ReadOnlyGraph, config: MacroConfig
) -> list[dict[str, Any]]:
    """The decisions inside the window that actually changed something."""
    rows = graph.find_nodes(
        ["DecisionAuditNode"],
        since=window.period_start,
        until=window.period_end,
        active_only=False,
        limit=max(config.max_nodes_per_kind, 1),
    )

    return [
        tidied
        for tidied in (tidy_row(row) for row in rows)
        if str(tidied.get("action") or "") in SHIFT_ACTIONS
        and str(tidied.get("status") or "") not in INERT_STATUSES
    ]


def _episodes_behind(
    decisions: list[dict[str, Any]], *, graph: ReadOnlyGraph
) -> tuple[str, ...]:
    """
    Which pieces of writing produced these decisions.

    Needed because a report records what it looked at, and for this kind the
    thing it looked at is reached through the finding each decision was made
    about rather than being named on the decision itself.
    """
    source_ids = sorted(
        {str(row.get("source_node_id") or "") for row in decisions} - {""}
    )
    if not source_ids:
        return ()

    found = {
        str(row.get("episode_id"))
        for row in graph.get_nodes_by_ids(source_ids)
        if row.get("episode_id")
    }
    return tuple(sorted(found))


def last_scan_at(graph: ReadOnlyGraph) -> datetime | None:
    """
    When the two-day scan last left a note behind.

    Read from the reports themselves rather than kept as separate state. The
    scan writes only when it finds something, so this is the last time
    something was found — which is what the spacing rule is actually about.
    """
    rows = graph.find_reports(report_type="SHADOW", limit=1)
    if not rows:
        return None

    created = tidy_row(rows[0]).get("created_at")
    if isinstance(created, datetime):
        return created
    try:
        return datetime.fromisoformat(str(created))
    except (TypeError, ValueError):
        logger.debug("could not read when the last shadow scan ran")
        return None


__all__ = ["SHIFT_ACTIONS", "INERT_STATUSES", "scan", "last_scan_at"]
