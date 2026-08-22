"""
Making somebody's stores, and checking they are all really there.

Two halves — a graph and a search index — and the order matters. The graph is
made first because a graph with no index is a person whose writing lands and
cannot be found, while an index with no graph is an index with nothing in it.
Of the two ways to be interrupted, the second is harmless and the first is
the one that looks like a working system holding an empty history.

That is why using somebody's stores checks them rather than assuming.
Provisioning can be interrupted — a process killed, a disk full — and the
resulting state is one where every write succeeds and every search comes back
empty. Goal 13b caught the same shape of failure once already, at the width
of a collection rather than the existence of one.

Repeating any of this is safe. Both halves are made only if missing, so a
check that runs on every first use costs nothing when everything is fine.
"""

from __future__ import annotations

import logging

from lumen.config import AppConfig
from lumen.stores.contracts import HalfProvisioned
from lumen.stores.keys import collection_name, graph_dir

logger = logging.getLogger(__name__)


def provision(user_id: str, *, config: AppConfig, open_graph, open_vectors) -> None:
    """
    Make sure this person has both of their stores.

    Safe to call for somebody who already has them, which is what lets it run
    on the way to handing out a handle rather than as a separate step
    somebody has to remember.

    The graph goes first. Interrupted after it, the person has a graph and no
    index — caught by `verify` on the next attempt. Interrupted the other way
    round they would have an index and no graph, which nothing would notice
    because there would be nothing to find.
    """
    directory = graph_dir(config.graph.db_root, user_id)
    directory.parent.mkdir(parents=True, exist_ok=True)

    graph = open_graph(str(directory))
    try:
        graph.init_schema()
    finally:
        graph.close()

    vectors = open_vectors(collection_name(user_id))
    try:
        vectors.init_collection()
    finally:
        vectors.close()

    logger.info("a person's stores were made ready", extra={"user_id": user_id})


def verify(stores, *, config: AppConfig) -> None:
    """
    Check that a person's stores are both really usable.

    Raises:
        HalfProvisioned: One of the two is missing or unusable. Reported
            rather than served, because an empty answer from a store that
            does not exist is indistinguishable from an empty answer from a
            person who has not written anything — and only one of those is
            worth telling somebody about.
    """
    try:
        stores.graph.count_by_type()
    except Exception as exc:  # noqa: BLE001 — any failure means the same thing
        raise HalfProvisioned(
            f"the graph for {stores.user_id} cannot be read"
        ) from exc

    try:
        stores.vectors.get_vectors([])
    except Exception as exc:  # noqa: BLE001
        raise HalfProvisioned(
            f"the search index for {stores.user_id} cannot be read; their "
            "history would be written and never found"
        ) from exc


__all__ = ["provision", "verify"]
