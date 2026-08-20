"""
Telling the assistant that something appears to be shifting right now.

The two-day scan can notice several beliefs branching or contradicting at
once, which is the shape of something moving while it happens rather than a
month later. Until now it wrote that down and nobody was told, which makes it
a note in a file rather than something the system knows.

This is the reading half, and it is deliberately small. It finds the most
recent alert, ignores anything old enough to have stopped being news, and
returns one sentence. Everything about *how* that sentence is used — whether
it fits the budget, whether the moment is right for it — belongs to the
briefing, not here.

It lives apart from the briefing for one reason: the thing that builds a
briefing is a pure function of what it is handed, and reaching a store from
inside it would be the first exception to that.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from lumen.config import MacroConfig
from lumen.graph.provider import ReadOnlyGraph
from lumen.graph.queries import tidy_row
from lumen.graph.rows import as_utc, read_moment
from lumen.schemas.enums import ReportType

logger = logging.getLogger(__name__)

# How the alert is worded to the assistant. Fixed rather than asked of a
# model: the report already contains a sentence a model wrote about the
# finding, and rewording it here would be a second model's opinion of a first
# model's opinion.
ALERT_TEMPLATE = "Something may be shifting right now: {summary}"


class ShadowAlertReader:
    """
    Finds the most recent alert worth mentioning, if there is one.

    Holds a reader and nothing else. Every failure comes back as "no alert",
    because a turn is not worth refusing over a notification.
    """

    def __init__(
        self, graph: ReadOnlyGraph, *, config: MacroConfig | None = None
    ) -> None:
        self._graph = graph
        self._config = config or MacroConfig()

    def current(self, now: datetime) -> str | None:
        """
        The alert to mention on this turn, or nothing.

        Nothing is the common answer and the cheap one: alerts are rare, and
        most conversations happen when nothing is shifting.
        """
        try:
            rows = self._graph.find_reports(
                report_type=ReportType.SHADOW.value, limit=1
            )
        except Exception:
            logger.warning("could not check for alerts", exc_info=True)
            return None

        if not rows:
            return None

        report = tidy_row(rows[0])
        if not self._still_news(report, now):
            return None

        summary = _summary_of(report)
        if not summary:
            return None
        return ALERT_TEMPLATE.format(summary=summary)

    def _still_news(self, report: dict, now: datetime) -> bool:
        """
        Whether an alert is recent enough to be worth saying out loud.

        An alert about a burst last month is not news, and mentioning it
        would make the assistant sound like it had lost track of when things
        happened. The window is the same one the scan uses to decide not to
        repeat itself.
        """
        raised = read_moment(report.get("created_at"))
        if raised is None:
            return False
        age = as_utc(now) - as_utc(raised)
        return age <= timedelta(hours=max(self._config.shadow_repeat_hours, 1))


def _summary_of(report: dict) -> str:
    """
    The sentence a shadow report holds, if it holds one.

    A report's body is kept as text, because there is no column type for a
    document, so it arrives as a run of JSON. Read defensively throughout:
    one written by an older version of the code should cost the alert rather
    than the turn.
    """
    content = _body(report.get("report_content"))
    finding = content.get("shadow_micro_shift")
    if not isinstance(finding, dict) or not finding.get("detected"):
        return ""
    return str(finding.get("summary") or "").strip()


def _body(raw: object) -> dict:
    """A report's body, read back from however it was stored."""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(str(raw))
    except (TypeError, ValueError):
        logger.debug("an alert's body could not be read")
        return {}
    return parsed if isinstance(parsed, dict) else {}


__all__ = ["ShadowAlertReader", "ALERT_TEMPLATE"]
