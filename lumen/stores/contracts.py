"""
What a person's stores are, and what can go wrong with them.

`UserStores` is deliberately small: a graph, a search index, and whose they
are. Everything above it takes one of these instead of reaching for a store
of its own, which is the whole of how isolation is enforced — there is no
shared table to forget to filter, because there is no shared table.
"""

from __future__ import annotations

from dataclasses import dataclass

from lumen.graph.provider import GraphProvider
from lumen.vector.provider import VectorProvider


@dataclass(frozen=True)
class UserStores:
    """
    One person's history, in the two places it lives.

    Handed out by the registry and never constructed anywhere else, so that
    "which person's graph is this" has exactly one answer and it is written
    on the object.

    Attributes:
        user_id: Whose these are.
        graph: Their knowledge graph, writable.
        vectors: Their search index.
    """

    user_id: str
    graph: GraphProvider
    vectors: VectorProvider


class StoreError(RuntimeError):
    """Something is wrong with a person's stores."""


class HalfProvisioned(StoreError):
    """
    A person has some of their stores and not all of them.

    The failure worth having its own name. A graph without a search index is
    somebody for whom every write succeeds and nothing is ever findable —
    which looks, from the outside, exactly like a person who has never
    written anything. Detected when their stores are first used, and reported
    rather than served.
    """


class StoresClosed(StoreError):
    """The registry has been shut down and cannot hand anything out."""


__all__ = ["UserStores", "StoreError", "HalfProvisioned", "StoresClosed"]
