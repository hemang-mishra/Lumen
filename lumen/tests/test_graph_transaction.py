"""
Tests for grouping graph writes so they all land or none of them do.

Run against a real Kuzu database rather than a stand-in. A fake asked
whether it rolled back will always say yes, which is precisely the claim
worth checking — everything else in the pipeline's saving relies on it.
"""

from __future__ import annotations

import threading

import pytest


def _observation(node_id: str, content: str = "a thing that happened") -> dict:
    """The smallest valid observation, for tests that only count rows."""
    return {
        "node_id": node_id,
        "episode_id": "ep_test",
        "occurred_at": "2026-06-11T20:00:00Z",
        "created_at": "2026-06-11T20:00:00Z",
        "valid_from": "2026-06-11T20:00:00Z",
        "type": "PATTERN",
        "content": content,
        "signal_strength": "STANDARD",
        "provenance": "USER_GENERATED",
        "verification_status": "IMPLICIT",
        "extraction_confidence": "STANDARD",
        "status": "ACTIVE",
        "extraction_model": "fake",
        "extraction_attempt": 1,
    }


class TestCommitting:
    def test_writes_inside_a_transaction_are_kept(self, graph_store):
        with graph_store.transaction():
            graph_store.write_node("ObservationNode", _observation("obs_kept"))

        assert graph_store.get_node("obs_kept") is not None

    def test_several_writes_land_together(self, graph_store):
        with graph_store.transaction():
            for index in range(5):
                graph_store.write_node("ObservationNode", _observation(f"obs_{index}"))

        assert all(graph_store.get_node(f"obs_{i}") is not None for i in range(5))

    def test_a_bookkeeping_update_joins_the_open_transaction(
        self, graph_store, seed_pattern
    ):
        from datetime import UTC, datetime

        seed_pattern("pat_here")

        with graph_store.transaction():
            graph_store.record_reinforcement("pat_here", at=datetime.now(UTC))

        assert graph_store.get_node("pat_here")["evidence_count"] >= 1


class TestRollingBack:
    def test_a_failure_undoes_every_write_in_the_group(self, graph_store):
        # This is the guarantee the whole save step is built on: an entry
        # that breaks partway through leaves nothing behind, rather than a
        # half-written entry that reads as complete.
        with pytest.raises(RuntimeError, match="something broke"):
            with graph_store.transaction():
                graph_store.write_node("ObservationNode", _observation("obs_a"))
                graph_store.write_node("ObservationNode", _observation("obs_b"))
                raise RuntimeError("something broke")

        assert graph_store.get_node("obs_a") is None
        assert graph_store.get_node("obs_b") is None

    def test_a_failed_write_undoes_the_ones_before_it(self, graph_store):
        with pytest.raises(Exception):
            with graph_store.transaction():
                graph_store.write_node("ObservationNode", _observation("obs_good"))
                graph_store.write_node("NotARealTable", _observation("obs_bad"))

        assert graph_store.get_node("obs_good") is None

    def test_the_original_failure_reaches_the_caller(self, graph_store):
        class OwnError(Exception):
            pass

        with pytest.raises(OwnError):
            with graph_store.transaction():
                raise OwnError("mine")

    def test_the_database_still_works_after_a_rollback(self, graph_store):
        with pytest.raises(RuntimeError):
            with graph_store.transaction():
                graph_store.write_node("ObservationNode", _observation("obs_lost"))
                raise RuntimeError("nope")

        with graph_store.transaction():
            graph_store.write_node("ObservationNode", _observation("obs_after"))

        assert graph_store.get_node("obs_after") is not None


class TestNesting:
    def test_opening_a_second_transaction_is_refused(self, graph_store):
        # Silently reusing the outer one would protect a wider group of
        # writes than the caller asked for, and they would never find out.
        with graph_store.transaction():
            with pytest.raises(RuntimeError, match="already open"):
                with graph_store.transaction():
                    pass

    def test_a_database_that_abandoned_the_transaction_itself_is_handled(
        self, graph_store
    ):
        # A statement the database rejects is enough for it to drop the
        # transaction on its own, and asking it to roll back after that is
        # an error. Treating that as a new problem would replace the real
        # failure with a complaint about the cleanup.
        graph_store.write_node("ObservationNode", _observation("obs_taken"))

        with pytest.raises(Exception, match="duplicated primary key"):
            with graph_store.transaction():
                graph_store.write_node("ObservationNode", _observation("obs_taken"))

        assert graph_store.get_node("obs_taken") is not None

    def test_another_thread_waits_for_its_turn_rather_than_being_refused(
        self, graph_store
    ):
        # A second thread is not asking for a smaller group of writes inside
        # this one — it is asking for its own, and it can have it once this
        # one finishes. Refusing it would be answering a question it did not
        # ask. This is what lets a web server share one provider between the
        # routes that read and the background thread that imports.
        outcome: list[str] = []
        inside = threading.Event()
        release = threading.Event()

        def second_writer() -> None:
            with graph_store.transaction():
                graph_store.write_node("ObservationNode", _observation("obs_second"))
            outcome.append("committed")

        with graph_store.transaction():
            graph_store.write_node("ObservationNode", _observation("obs_first"))
            thread = threading.Thread(target=second_writer)
            thread.start()
            inside.set()
            # The other thread is now blocked on the lock. Nothing it wrote
            # can have landed yet, because it has not been allowed to start.
            release.wait(timeout=0.2)
            assert outcome == []

        thread.join(timeout=10)
        assert outcome == ["committed"]
        assert graph_store.get_node("obs_first") is not None
        assert graph_store.get_node("obs_second") is not None

    def test_a_read_cannot_land_inside_someone_elses_open_transaction(
        self, graph_store
    ):
        # The failure this prevents: a read arriving mid-import runs on the
        # same connection, so without the lock it would join the importer's
        # transaction and report an episode that has not been committed and
        # might still be rolled back.
        seen: list[object] = []

        def reader() -> None:
            seen.append(graph_store.get_node("obs_uncommitted"))

        thread = threading.Thread(target=reader)
        try:
            with graph_store.transaction():
                graph_store.write_node(
                    "ObservationNode", _observation("obs_uncommitted")
                )
                thread.start()
                thread.join(timeout=0.2)
                # Still blocked: it has not been allowed to look yet.
                assert seen == []
                raise RuntimeError("the import failed after all")
        except RuntimeError:
            pass

        thread.join(timeout=10)
        assert seen == [None]

    def test_a_rollback_problem_that_is_not_about_state_is_not_swallowed(
        self, graph_store, monkeypatch
    ):
        # Treating "the database already abandoned it" as done is safe.
        # Treating every rollback error that way would hide a real one.
        def refuse(query, *args, **kwargs):
            if query == "ROLLBACK":
                raise RuntimeError("the disk is on fire")
            return None

        with pytest.raises(RuntimeError, match="disk is on fire"):
            with graph_store.transaction():
                monkeypatch.setattr(graph_store.conn, "execute", refuse)
                raise RuntimeError("the original problem")

    def test_a_rollback_clears_the_way_for_the_next_transaction(self, graph_store):
        with pytest.raises(RuntimeError):
            with graph_store.transaction():
                raise RuntimeError("boom")

        # Would raise "already open" if the failed one had not been closed out.
        with graph_store.transaction():
            graph_store.write_node("ObservationNode", _observation("obs_next"))

        assert graph_store.get_node("obs_next") is not None
