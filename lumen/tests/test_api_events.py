"""
Tests for saying what is happening while it happens.

Two things matter here and both are about not mattering too much. Nothing is
owed to somebody who was not watching — the backlog exists so a page that has
just opened is not blank, not as a record. And a listener that has fallen
behind loses its own messages rather than anybody else's, because a browser
left open on a sleeping laptop must not be able to hold up the pipeline.
"""

from __future__ import annotations

import asyncio

import pytest

from lumen.api.events import LISTENER_BACKLOG, EventBus


@pytest.fixture
def bus():
    """A bus with a short backlog, bound to nothing yet."""
    return EventBus(history=5)


class TestPublishing:
    def test_something_published_is_kept_for_whoever_opens_a_page(self, bus):
        bus.publish("run_started", session_id="sess_1")

        recent = bus.recent()

        assert [event.kind for event in recent] == ["run_started"]
        assert recent[0].payload == {"session_id": "sess_1"}

    def test_publishing_with_nobody_listening_is_fine(self, bus):
        # Which is most of the time.
        assert bus.publish("run_finished").kind == "run_finished"

    def test_the_backlog_is_short_and_keeps_the_newest(self, bus):
        # A system that kept every event would be keeping a second, worse
        # copy of what the graph and the job records already hold.
        for index in range(9):
            bus.publish("job_ran", n=index)

        held = [event.payload["n"] for event in bus.recent()]

        assert held == [4, 5, 6, 7, 8]

    def test_asking_for_fewer_gives_the_most_recent(self, bus):
        for index in range(5):
            bus.publish("job_ran", n=index)

        assert [e.payload["n"] for e in bus.recent(2)] == [3, 4]

    def test_everything_carries_when_it_happened(self, bus):
        assert bus.publish("day_nudge").at is not None


def on_a_loop(work):
    """
    Run one piece of async work on a loop of its own.

    Written this way rather than with an async-test plugin: the bus is used
    from ordinary threads and from the loop, and a test that owns its loop
    explicitly is a closer match to how it actually runs.
    """
    return asyncio.run(work())


class TestListening:
    def test_a_listener_hears_what_is_published_after_it_arrives(self, bus):
        async def go():
            with bus.subscribe() as queue:
                bus.publish("run_started", session_id="sess_1")
                return await asyncio.wait_for(queue.get(), timeout=2)

        assert on_a_loop(go).kind == "run_started"

    def test_nothing_from_before_is_replayed(self, bus):
        # Somebody who was not looking is not owed a backlog. What they
        # missed is readable from the endpoints that own it.
        async def go():
            bus.publish("run_started", session_id="before")
            with bus.subscribe() as queue:
                return queue.empty()

        assert on_a_loop(go) is True

    def test_two_listeners_both_hear_it(self, bus):
        async def go():
            with bus.subscribe() as first, bus.subscribe() as second:
                bus.publish("report_written")
                await asyncio.sleep(0)
                return first.qsize(), second.qsize()

        assert on_a_loop(go) == (1, 1)

    def test_a_listener_that_leaves_stops_being_given_things(self, bus):
        async def go():
            with bus.subscribe():
                pass
            return bus.listeners

        assert on_a_loop(go) == 0

    def test_a_listener_too_far_behind_loses_its_own_oldest(self, bus):
        # Its own, and nobody else's. This is the rule that keeps a stalled
        # browser from stalling a pipeline.
        async def go():
            with bus.subscribe() as queue:
                for index in range(LISTENER_BACKLOG + 10):
                    bus.publish("job_ran", n=index)
                await asyncio.sleep(0)
                return queue.qsize(), queue.get_nowait().payload["n"]

        assert on_a_loop(go) == (LISTENER_BACKLOG, 10)

    def test_publishing_from_another_thread_reaches_the_loop(self, bus):
        # The real case: the pipeline publishes from a worker thread while
        # the listeners are on the event loop.
        import threading

        async def go():
            with bus.subscribe() as queue:
                threading.Thread(
                    target=lambda: bus.publish("run_finished", status="COMPLETE")
                ).start()
                return await asyncio.wait_for(queue.get(), timeout=2)

        assert on_a_loop(go).payload == {"status": "COMPLETE"}

    def test_publishing_before_anything_is_listening_does_not_raise(self, bus):
        # The bus exists before any loop does, because the importer announces
        # what it does and is built first.
        assert bus.publish("run_started").kind == "run_started"


class TestThroughTheApi:
    def test_the_backlog_is_readable(self, api_client):
        api_client.app.state.events.publish("run_finished", status="COMPLETE")

        body = api_client.get("/events").json()

        assert body["count"] == 1
        assert body["events"][0]["kind"] == "run_finished"

    def test_an_empty_backlog_is_an_empty_answer(self, api_client):
        assert api_client.get("/events").json() == {
            "events": [],
            "count": 0,
            "listeners": 0,
        }

    def test_how_many_are_watching_is_reported(self, api_client):
        # "Nothing is happening" and "nothing is connected" look identical
        # from a page that sees no events, and they are fixed differently.
        assert api_client.get("/events").json()["listeners"] == 0

    def test_the_socket_sends_what_is_published(self, api_client):
        with api_client.websocket_connect("/events/ws") as socket:
            api_client.app.state.events.publish("run_started", session_id="sess_1")
            message = socket.receive_json()

        assert message["kind"] == "run_started"
        assert message["payload"] == {"session_id": "sess_1"}
