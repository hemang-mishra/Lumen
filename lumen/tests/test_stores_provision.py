"""
Tests for making somebody's stores, and noticing when only half of them exist.

The failure worth catching is specific and quiet: a person whose graph exists
and whose search index does not is a person for whom every write succeeds and
nothing is ever findable. From the outside that looks exactly like somebody
who has never written anything — which is why using a person's stores checks
them rather than assuming.

The order is the other half. The graph is made first, so an interruption
leaves the state that gets caught rather than the one that does not.
"""

from __future__ import annotations

import pytest

from lumen.config import AppConfig, GraphConfig, VectorConfig
from lumen.stores.contracts import HalfProvisioned, UserStores
from lumen.stores.keys import collection_name, graph_dir
from lumen.stores.provision import provision, verify


class Store:
    """A stand-in store that records what was done to it."""

    def __init__(self, name: str, *, broken: bool = False) -> None:
        self.name = name
        self.broken = broken
        self.initialised = 0
        self.closed = 0

    def init_schema(self) -> None:
        self.initialised += 1

    def init_collection(self) -> None:
        self.initialised += 1

    def count_by_type(self):
        if self.broken:
            raise RuntimeError("this graph cannot be read")
        return {}

    def get_vectors(self, node_ids):
        if self.broken:
            raise RuntimeError("this collection does not exist")
        return {}

    def close(self) -> None:
        self.closed += 1


@pytest.fixture
def settings(tmp_path):
    return AppConfig(
        graph=GraphConfig(db_root=str(tmp_path / "graphs")),
        vector=VectorConfig(location=":memory:", vector_size=8),
    )


@pytest.fixture
def openers():
    """Openers that hand back recorded stand-ins, and the order they ran in."""
    made: list[str] = []
    stores: dict[str, Store] = {}

    def graph(path: str):
        made.append("graph")
        stores["graph"] = Store(path)
        return stores["graph"]

    def vectors(name: str):
        made.append("vectors")
        stores["vectors"] = Store(name)
        return stores["vectors"]

    return graph, vectors, made, stores


class TestMakingThem:
    def test_both_halves_are_made(self, settings, openers):
        graph, vectors, made, stores = openers

        provision("usr_a", config=settings, open_graph=graph, open_vectors=vectors)

        assert stores["graph"].initialised == 1
        assert stores["vectors"].initialised == 1

    def test_the_graph_is_made_first(self, settings, openers):
        # Of the two ways to be interrupted, this leaves the one that gets
        # caught. The other way round leaves an index with nothing in it,
        # which nothing would ever notice.
        graph, vectors, made, _ = openers

        provision("usr_a", config=settings, open_graph=graph, open_vectors=vectors)

        assert made == ["graph", "vectors"]

    def test_the_directory_is_created(self, settings, openers):
        graph, vectors, _, _ = openers

        provision("usr_a", config=settings, open_graph=graph, open_vectors=vectors)

        assert graph_dir(settings.graph.db_root, "usr_a").parent.exists()

    def test_each_person_gets_their_own_names(self, settings, openers):
        graph, vectors, _, stores = openers

        provision("usr_a", config=settings, open_graph=graph, open_vectors=vectors)

        assert "usr_a" in stores["graph"].name
        assert stores["vectors"].name == collection_name("usr_a")

    def test_doing_it_twice_is_harmless(self, settings, openers):
        # Which is what lets it run on the way to handing out a handle,
        # rather than as a step somebody has to remember.
        graph, vectors, _, _ = openers

        provision("usr_a", config=settings, open_graph=graph, open_vectors=vectors)
        provision("usr_a", config=settings, open_graph=graph, open_vectors=vectors)

    def test_what_it_opened_it_closes(self, settings, openers):
        # Provisioning is not a lease. Leaving these open would mean a handle
        # nobody is tracking, on a store that allows exactly one.
        graph, vectors, _, stores = openers

        provision("usr_a", config=settings, open_graph=graph, open_vectors=vectors)

        assert stores["graph"].closed == 1
        assert stores["vectors"].closed == 1

    def test_an_unsafe_identifier_never_becomes_a_directory(self, settings, openers):
        from lumen.stores.keys import UnsafeUserKey

        graph, vectors, _, _ = openers

        with pytest.raises(UnsafeUserKey):
            provision(
                "../../etc", config=settings, open_graph=graph, open_vectors=vectors
            )


class TestCheckingThem:
    def test_a_complete_pair_passes(self, settings):
        stores = UserStores(user_id="usr_a", graph=Store("g"), vectors=Store("v"))

        verify(stores, config=settings)

    def test_a_graph_that_cannot_be_read_is_reported(self, settings):
        stores = UserStores(
            user_id="usr_a", graph=Store("g", broken=True), vectors=Store("v")
        )

        with pytest.raises(HalfProvisioned, match="graph"):
            verify(stores, config=settings)

    def test_a_missing_index_is_reported_rather_than_served(self, settings):
        # The one that matters. Served instead, it is a person whose writing
        # lands and can never be found — indistinguishable from a person who
        # has written nothing.
        stores = UserStores(
            user_id="usr_a", graph=Store("g"), vectors=Store("v", broken=True)
        )

        with pytest.raises(HalfProvisioned) as reported:
            verify(stores, config=settings)

        assert "never found" in str(reported.value)

    def test_the_report_names_the_person(self, settings):
        stores = UserStores(
            user_id="usr_a", graph=Store("g", broken=True), vectors=Store("v")
        )

        with pytest.raises(HalfProvisioned, match="usr_a"):
            verify(stores, config=settings)


class TestCheckedOnFirstUse:
    def test_a_half_made_person_is_caught_when_their_stores_are_borrowed(
        self, settings
    ):
        from lumen.stores import StoreRegistry

        class _Broken(StoreRegistry):
            def _open(self, key):
                return UserStores(
                    user_id=key, graph=Store("g"), vectors=Store("v", broken=True)
                )

        held = _Broken(settings)

        with pytest.raises(HalfProvisioned):
            with held.lease("usr_a"):
                pass

    def test_a_good_pair_is_only_checked_once(self, settings):
        from lumen.stores import StoreRegistry

        checks: list[int] = []

        class _Counting(StoreRegistry):
            def _open(self, key):
                return UserStores(user_id=key, graph=Store("g"), vectors=Store("v"))

            def _check(self, stores):
                checks.append(1)

        held = _Counting(settings)
        for _ in range(3):
            with held.lease("usr_a"):
                pass

        assert checks == [1]
