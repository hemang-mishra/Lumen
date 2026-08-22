"""
Tests for the one place a store handle comes from.

Two rules carry the whole design, and both are here.

**A handle in use is never closed.** A background extraction run holds
somebody's graph for minutes; a registry that closed it because six other
people signed in would corrupt the run it interrupted.

**Everybody working on one person's graph holds the same handle.** Not an
economy — an embedded graph takes an exclusive lock per directory, so a
second handle is refused rather than slow. This is also what makes a web
request and a background run able to share a graph safely, which until now
was true only because there was one of everything.

Driven with stand-in stores, because what is worth checking here is counting
borrowers and choosing what to close, not whether a database works.
"""

from __future__ import annotations

import threading

import pytest

from lumen.config import AppConfig, GraphConfig
from lumen.stores import StoreRegistry, StoresClosed, UserStores


class Fake:
    """A store that records whether it has been closed."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def registry(tmp_path):
    """A registry over stand-in stores, with a chosen ceiling."""

    def _build(ceiling: int = 32):
        opened: dict[str, UserStores] = {}

        class _Counting(StoreRegistry):
            def _open(self, key):
                stores = UserStores(
                    user_id=key, graph=Fake(f"graph:{key}"), vectors=Fake(f"index:{key}")
                )
                opened[key] = stores
                return stores

            def _check(self, stores):
                """Nothing to check — these were made here."""

        held = _Counting(
            AppConfig(
                graph=GraphConfig(
                    db_root=str(tmp_path / "graphs"), max_open_graphs=ceiling
                )
            )
        )
        held.opened = opened
        return held

    return _build


class TestBorrowingAndReturning:
    def test_a_lease_hands_over_that_person_s_stores(self, registry):
        held = registry()

        with held.lease("usr_a") as stores:
            assert stores.user_id == "usr_a"
            assert stores.graph.name == "graph:usr_a"

    def test_two_people_get_two_different_sets(self, registry):
        held = registry()

        with held.lease("usr_a") as a, held.lease("usr_b") as b:
            assert a.graph is not b.graph

    def test_the_same_person_twice_gets_the_same_handle(self, registry):
        # The rule that makes a web request and a background run able to
        # share a graph. A second handle on one directory is refused, not
        # slow.
        held = registry()

        with held.lease("usr_a") as first:
            with held.lease("usr_a") as second:
                assert first.graph is second.graph

    def test_a_handle_is_given_back_even_when_something_fails(self, registry):
        # A failed request that kept somebody's graph open would eventually
        # stop anybody else's from being opened at all.
        held = registry()

        with pytest.raises(RuntimeError):
            with held.lease("usr_a"):
                raise RuntimeError("the request failed")

        assert held.in_use() == 0

    def test_it_says_how_many_are_open_and_how_many_are_lent_out(self, registry):
        held = registry()

        with held.lease("usr_a"):
            assert held.open_count == 1
            assert held.in_use() == 1

        assert held.open_count == 1
        assert held.in_use() == 0


class TestTheCeiling:
    def test_it_closes_something_once_there_are_too_many(self, registry):
        held = registry(ceiling=2)

        for name in ("usr_a", "usr_b", "usr_c"):
            with held.lease(name):
                pass

        assert held.open_count == 2

    def test_the_one_closed_is_the_least_recently_used(self, registry):
        held = registry(ceiling=2)
        for name in ("usr_a", "usr_b"):
            with held.lease(name):
                pass
        # Touch the older one so the other becomes the stalest.
        with held.lease("usr_a"):
            pass

        with held.lease("usr_c"):
            pass

        assert held.opened["usr_b"].graph.closed is True
        assert held.opened["usr_a"].graph.closed is False

    def test_a_handle_in_use_is_never_the_one_closed(self, registry):
        # The rule the whole design rests on. Closing a graph mid-write
        # corrupts the run it interrupted.
        held = registry(ceiling=1)

        with held.lease("usr_a"):
            with held.lease("usr_b"):
                pass
            assert held.opened["usr_a"].graph.closed is False

    def test_being_briefly_over_the_ceiling_beats_waiting(self, registry):
        # Memory is the cost of being over; a corrupted entry is the cost of
        # closing something mid-write.
        held = registry(ceiling=1)

        with held.lease("usr_a"), held.lease("usr_b"), held.lease("usr_c"):
            assert held.open_count == 3

    def test_both_halves_of_a_person_s_stores_are_closed(self, registry):
        held = registry(ceiling=1)

        with held.lease("usr_a"):
            pass
        with held.lease("usr_b"):
            pass

        assert held.opened["usr_a"].graph.closed is True
        assert held.opened["usr_a"].vectors.closed is True

    def test_a_closed_store_is_reopened_next_time(self, registry):
        held = registry(ceiling=1)
        with held.lease("usr_a"):
            pass
        with held.lease("usr_b"):
            pass

        with held.lease("usr_a") as again:
            assert again.graph.closed is False


class TestSeveralThreads:
    def test_two_threads_asking_for_one_person_share_a_handle(self, registry):
        held = registry()
        seen: list[int] = []
        ready = threading.Barrier(2)

        def borrow():
            ready.wait(timeout=5)
            with held.lease("usr_a") as stores:
                seen.append(id(stores.graph))

        threads = [threading.Thread(target=borrow) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        assert len(set(seen)) == 1

    def test_many_threads_on_many_people_do_not_collide(self, registry):
        held = registry(ceiling=4)
        failures: list[Exception] = []

        def borrow(name: str):
            try:
                for _ in range(5):
                    with held.lease(name) as stores:
                        assert stores.user_id == name
            except Exception as exc:  # noqa: BLE001
                failures.append(exc)

        threads = [
            threading.Thread(target=borrow, args=(f"usr_{index}",))
            for index in range(8)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert failures == []


class TestShuttingDown:
    def test_closing_shuts_everything(self, registry):
        held = registry()
        for name in ("usr_a", "usr_b"):
            with held.lease(name):
                pass

        held.close()

        assert all(s.graph.closed for s in held.opened.values())
        assert held.open_count == 0

    def test_nothing_can_be_borrowed_afterwards(self, registry):
        held = registry()
        held.close()

        with pytest.raises(StoresClosed):
            with held.lease("usr_a"):
                pass

    def test_a_store_that_will_not_close_does_not_break_the_shutdown(self, registry):
        held = registry()

        with held.lease("usr_a"):
            pass

        def refuse():
            raise RuntimeError("this one is stuck")

        held.opened["usr_a"].graph.close = refuse

        held.close()

        assert held.open_count == 0

    def test_an_unsafe_identifier_never_reaches_a_path(self, registry):
        from lumen.stores.keys import UnsafeUserKey

        held = registry()

        with pytest.raises(UnsafeUserKey):
            with held.lease("../../etc"):
                pass
