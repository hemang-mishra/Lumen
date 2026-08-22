"""
The one place a store handle comes from.

Everything above this takes a person's stores rather than opening its own,
and that is the whole of how one person's history is kept out of another's.
There is no shared table to forget to filter, because there is no shared
table — every query in the system stays exactly as it was written and is
automatically about one person, because of which handle it was given.

Three rules do the work.

**Everybody working on one person's graph holds the same handle.** Not an
optimisation: the graph is embedded and takes an exclusive lock per
directory, so a second handle on one directory is not slow, it is refused.
This is also the answer to how a web request and a background extraction run
share a graph safely, which until now was true by accident of there being
only one.

**The two halves are kept apart differently, because the stores differ.**
A graph is a directory per person, opened and closed. The search index is one
storage holding a collection per person, because it refuses a second
connection to one folder exactly as the graph refuses a second handle on one
directory — so the connection is shared and only the collection differs. Both
are structural: there is no shared table either way.

**A handle in use is never closed.** Stores are lent out and returned, and
only returned ones can be evicted. A background run holds somebody's graph
for minutes; closing it underneath because six other people signed in would
corrupt the run it interrupted.

**There is a ceiling, and it is a real one.** Every open graph is file
handles and memory. Past the ceiling the least recently used *idle* store is
closed, and reopening costs milliseconds next time somebody needs it.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from lumen.config import AppConfig
from lumen.stores.contracts import StoresClosed, UserStores
from lumen.stores.keys import collection_name, graph_dir, user_key
from lumen.stores.provision import provision, verify

logger = logging.getLogger(__name__)


@dataclass
class _Held:
    """
    One person's open stores, and how many callers are using them.

    The count is what makes eviction safe. Nothing with a borrower is ever
    closed, however long ago it was last opened.
    """

    stores: UserStores
    borrowers: int = 0
    verified: bool = False

    @property
    def idle(self) -> bool:
        """Whether anything is currently using this."""
        return self.borrowers == 0


class StoreRegistry:
    """
    Hands out one person's stores at a time, and keeps a few warm.

    How stores are opened is injected, so the whole of this can be exercised
    with stand-ins and no disk — which matters, because the rules worth
    testing here are about counting borrowers and choosing what to close,
    not about databases.
    """

    def __init__(
        self,
        config: AppConfig | None = None,
        *,
        open_graph=None,
        open_vectors=None,
    ) -> None:
        self._config = config or AppConfig()
        self._open_graph = open_graph or _default_graph
        self._vectors = _VectorSource(self._config, open_vectors)
        self._held: OrderedDict[str, _Held] = OrderedDict()
        self._lock = threading.Lock()
        self._closed = False

    @contextmanager
    def lease(self, user_id: str) -> Iterator[UserStores]:
        """
        Borrow one person's stores for the length of a block.

        Provisions them if they do not exist yet, and checks them the first
        time they are used — a person whose graph exists and whose index does
        not is reported rather than served as an empty history.

        The handle is returned however the block ends, which is what keeps a
        failed request from pinning somebody's graph open forever.
        """
        held = self._borrow(user_id)
        try:
            yield held.stores
        finally:
            self._give_back(user_id)

    def close(self) -> None:
        """
        Shut everything.

        Closes what is open regardless of borrowers, because this runs when
        the process is stopping and something still holding a handle then is
        not going to give it back.
        """
        with self._lock:
            self._closed = True
            held = list(self._held.values())
            self._held.clear()

        for entry in held:
            _shut(entry.stores)
        self._vectors.close()
        logger.info("every open store was closed", extra={"closed": len(held)})

    @property
    def open_count(self) -> int:
        """How many people's stores are open right now."""
        with self._lock:
            return len(self._held)

    def in_use(self) -> int:
        """How many of them are currently lent out."""
        with self._lock:
            return sum(1 for entry in self._held.values() if not entry.idle)

    # ------------------------------------------------------------------
    # Borrowing and returning
    # ------------------------------------------------------------------

    def _borrow(self, user_id: str) -> _Held:
        """
        Find or open this person's stores and mark them as in use.

        Opening happens inside the lock. That is a real cost — one person's
        first request briefly delays everybody's — and the alternative is two
        callers opening the same directory at once, which the graph refuses
        outright.
        """
        key = user_key(user_id)

        with self._lock:
            if self._closed:
                raise StoresClosed("the store registry has been shut down")

            entry = self._held.get(key)
            if entry is None:
                entry = _Held(stores=self._open(key))
                self._held[key] = entry

            entry.borrowers += 1
            self._held.move_to_end(key)

            if not entry.verified:
                self._check(entry.stores)
                entry.verified = True

            return entry

    def _give_back(self, user_id: str) -> None:
        """Hand a person's stores back, and close something if there are too many."""
        key = user_key(user_id)
        surplus: list[UserStores] = []

        with self._lock:
            entry = self._held.get(key)
            if entry is not None:
                entry.borrowers = max(entry.borrowers - 1, 0)
            surplus = self._evict_locked()

        for stores in surplus:
            _shut(stores)

    def _evict_locked(self) -> list[UserStores]:
        """
        Choose what to close, now that something has been returned.

        Only idle entries, least recently used first. A person being used is
        skipped rather than waited for, so a busy deployment can sit above
        its ceiling for a moment instead of blocking — being briefly over is
        a memory cost, and closing a graph mid-write is a corrupted entry.
        """
        ceiling = max(int(self._config.graph.max_open_graphs), 1)
        if len(self._held) <= ceiling:
            return []

        closing: list[UserStores] = []
        for key, entry in list(self._held.items()):
            if len(self._held) - len(closing) <= ceiling:
                break
            if entry.idle:
                closing.append(entry.stores)
                del self._held[key]

        if closing:
            logger.info(
                "stores were closed to stay under the limit",
                extra={"closed": len(closing), "open": len(self._held)},
            )
        return closing

    def _check(self, stores: UserStores) -> None:
        """
        Confirm a person's stores are both really usable, the first time.

        Its own method so that something standing in for the real stores can
        say it has nothing to check. A stand-in that had to pretend to be a
        graph well enough to pass this would be a stand-in nobody could write.
        """
        verify(stores, config=self._config)

    def _open(self, key: str) -> UserStores:
        """Make sure this person's stores exist, then open them."""
        provision(
            key,
            config=self._config,
            open_graph=self._open_graph,
            open_vectors=self._vectors.open,
        )
        return UserStores(
            user_id=key,
            graph=self._open_graph(str(graph_dir(self._config.graph.db_root, key))),
            vectors=self._vectors.open(collection_name(key)),
        )


def _shut(stores: UserStores) -> None:
    """
    Close one person's stores, and never let that fail anything.

    A store that will not close is a warning. Raising here would take down
    whichever request happened to be the one that returned a handle.
    """
    for store in (stores.graph, stores.vectors):
        try:
            store.close()
        except Exception:  # noqa: BLE001 — closing is never worth an error
            logger.warning(
                "a store could not be closed",
                exc_info=True,
                extra={"user_id": stores.user_id},
            )


class _VectorSource:
    """
    One connection to the search index, and a collection per person.

    Exists because a file-backed index refuses a second connection to the
    same folder — so unlike the graph, where each person gets a handle of
    their own, everybody's collections have to come through one connection.

    The connection is owned here rather than by whichever provider happened
    to be made first — provisioning opens and closes a provider on the way
    past, and that would otherwise shut the connection everybody else is
    still using.
    """

    def __init__(self, config: AppConfig, opener=None) -> None:
        self._config = config
        self._opener = opener
        self._shared = None
        self._lock = threading.Lock()

    def open(self, collection: str):
        """A provider bound to one person's collection."""
        if self._opener is not None:
            return self._opener(collection)

        from lumen.vector.qdrant_impl import QdrantVectorProvider, open_client

        with self._lock:
            if self._shared is None:
                self._shared = open_client(self._config.vector.location)
            return QdrantVectorProvider(
                location=self._config.vector.location,
                collection_name=collection,
                vector_size=self._config.vector.vector_size,
                client=self._shared,
            )

    def close(self) -> None:
        """Close the shared connection, if one was ever opened."""
        with self._lock:
            if self._shared is not None:
                try:
                    self._shared.close()
                except Exception:  # noqa: BLE001 — closing is never worth an error
                    logger.warning("the search index could not be closed", exc_info=True)
                self._shared = None


def _default_graph(path: str):
    """Open a graph on disk. Imported late so naming this costs no driver."""
    from lumen.graph.kuzu_impl import KuzuGraphProvider

    return KuzuGraphProvider(path)


__all__ = ["StoreRegistry"]
