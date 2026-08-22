"""
Keeping count of which records keep turning out to be the useful ones.

A record that is repeatedly the right thing to bring up is telling you
something — usually that it sits close to whatever the person keeps circling
back to. Counting that costs almost nothing and gives ranking a signal it
cannot get any other way, since nothing else in the graph knows what has
actually helped.

Three rules keep the count honest, and all three are about what *not* to
count.

Only what reached the assistant counts. A search finds a dozen candidates and
the briefing keeps three; counting all twelve would measure what the search
engine likes rather than what helped, and that number then feeds back into
what the search engine finds.

Each record counts once a day. Somebody who spends a whole conversation on
one subject has one concern, not twenty, and counting each turn would let a
single afternoon outrank years of history permanently.

And none of it happens while anybody is waiting. This runs after the reply
has gone out, and a failure is logged and dropped — a lost count costs a
record a fraction of a point of ranking, where a conversation stalled behind
a database write costs the person the thing they came for.
"""

from __future__ import annotations

import logging
from datetime import datetime

from lumen.config import ScoringConfig
from lumen.stores import StoreRegistry
from lumen.query.assembly.contracts import AssembledContext
from lumen.query.session import ChatSession

logger = logging.getLogger(__name__)


class QueryHitRecorder:
    """
    Notes which records a turn actually used.

    An object rather than a function because it holds the settings that can
    switch it off and the registry it borrows a graph from, and because the
    thing that calls it — the conversation — should be able to be handed one
    that does nothing without knowing that is what it got.

    Which graph is decided per turn, from whoever is talking. There is one
    per person, so an object holding "the graph" would be counting somebody
    else's records.
    """

    def __init__(
        self, stores: StoreRegistry, *, config: ScoringConfig | None = None
    ) -> None:
        self._stores = stores
        self._config = config or ScoringConfig()

    def note(
        self, session: ChatSession, context: AssembledContext, *, at: datetime
    ) -> int:
        """
        Count the records this turn put in front of the assistant.

        Returns how many were counted. Nothing raises out of here: every
        failure ends up in the log and the conversation carries on, because
        the conversation is the part that matters and this is not.
        """
        if not self._config.frequency_enabled:
            return 0

        fresh = session.claim_query_hits(item.node_id for item in context.items)
        if not fresh:
            return 0

        try:
            with self._stores.lease(session.user_id) as stores:
                counted = stores.graph.record_query_hits(fresh, at=at)
        except Exception:
            logger.warning(
                "the records this turn used could not be counted, so their "
                "standing is unchanged",
                exc_info=True,
                extra={"session_id": session.session_id, "wanted": len(fresh)},
            )
            return 0

        logger.debug(
            "counted the records this turn used",
            extra={
                "session_id": session.session_id,
                "offered": len(context.items),
                "counted": counted,
            },
        )
        return counted


__all__ = ["QueryHitRecorder"]
