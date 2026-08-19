"""
Tests for the web surface of the review queue.

Two things get checked at this level that nothing below it can: that the
answers come back in a shape a caller can draw a card from, and that the
three ways a request can be wrong are told apart — a bad answer, a missing
item, and a request that was valid when the card was drawn and has since
been overtaken.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from lumen.operational.enums import HitlItemStatus
from lumen.schemas.enums import ReconciliationAction


# Who the routes answer for. They read it from the configuration rather than
# from the request, since there is one person per deployment until identity
# lands, so the queue has to be filled under the same name.
WHOSE = "local"


@pytest.fixture
def review_client(api_client, reviewer):
    """The web client, with the review queue wired to the test databases."""
    from lumen.api.deps import get_reviewer

    api_client.app.dependency_overrides[get_reviewer] = lambda: reviewer
    api_client.app.state.reviewer = reviewer
    return api_client


@pytest.fixture
def asked(queued):
    """Put a question into the queue under the name the routes answer for."""

    def _build(**kwargs):
        kwargs.setdefault("user_id", WHOSE)
        return queued(**kwargs)

    return _build


@pytest.fixture
def seeded(graph_store, sample_pattern, sample_observation, sample_episode):
    """The records a question points at, actually in the graph."""
    graph_store.write_node("EpisodeNode", sample_episode)
    graph_store.write_node("PatternNode", sample_pattern)
    graph_store.write_node("ObservationNode", sample_observation)
    return sample_pattern


class TestListingWhatIsWaiting:
    """The queue itself."""

    def test_an_empty_queue_answers_with_nothing(self, review_client):
        response = review_client.get("/hitl")

        assert response.status_code == 200
        assert response.json()["cards"] == []

    def test_a_waiting_question_comes_back_with_its_answers(
        self, review_client, asked, seeded
    ):
        item = asked()

        body = review_client.get("/hitl").json()

        assert [card["item_id"] for card in body["cards"]] == [item.id]
        assert body["cards"][0]["options"]
        assert body["cards"][0]["question"]

    def test_the_page_size_is_capped(self, review_client):
        assert review_client.get("/hitl?limit=101").status_code == 422


class TestTheBadge:
    """How much is waiting, cheaply."""

    def test_it_reports_the_counts_and_the_ceiling(
        self, review_client, asked, seeded
    ):
        asked()

        body = review_client.get("/hitl/count").json()

        assert body["pending"] == 1
        assert body["visible"] == 1
        assert body["cap"] >= 1
        assert body["at_capacity"] is False
        assert body["oldest_asked_at"] is not None

    def test_it_settles_nothing(self, review_client, asked, seeded, ops_store, moment):
        item = asked(snooze_count=1, last_snoozed_at=moment - timedelta(days=400))

        review_client.get("/hitl/count")

        assert ops_store.hitl.get(item.id).status is HitlItemStatus.PENDING_HITL


class TestOneCard:
    """Fetching a single question."""

    def test_it_comes_back_in_full(self, review_client, asked, seeded):
        item = asked()

        body = review_client.get(f"/hitl/{item.id}").json()

        assert body["item_id"] == item.id
        assert body["source_text"]

    def test_an_unknown_item_is_not_found(self, review_client):
        assert review_client.get("/hitl/nothing").status_code == 404

    def test_somebody_elses_item_is_not_found(self, review_client, queued, seeded):
        item = queued(user_id="someone-else")

        assert review_client.get(f"/hitl/{item.id}").status_code == 404

    def test_an_item_with_nothing_recorded_says_so_rather_than_failing(
        self, review_client, asked, seeded
    ):
        item = asked(save_proposal=False)

        body = review_client.get(f"/hitl/{item.id}").json()

        assert body["answerable"] is False
        assert body["unanswerable_reason"]
        assert body["options"] == []


class TestAnswering:
    """The one route here that changes the graph."""

    def test_an_answer_is_carried_out(
        self, review_client, asked, seeded, make_proposal, make_item,
        sample_observation,
    ):
        item = asked(
            proposal=make_proposal(item=make_item(node_id=sample_observation.node_id))
        )

        response = review_client.post(
            f"/hitl/{item.id}/resolve", json={"choice": "APPROVE"}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["action_taken"] == ReconciliationAction.REINFORCE.value
        assert body["new_audit_node_id"]

    def test_an_answer_the_card_did_not_offer_is_a_bad_request(
        self, review_client, asked, seeded
    ):
        item = asked()

        response = review_client.post(
            f"/hitl/{item.id}/resolve", json={"choice": "ACTION_B"}
        )

        assert response.status_code == 400
        assert response.json()["error"] == "bad_request"

    def test_a_word_that_is_not_an_answer_at_all_is_rejected(
        self, review_client, asked, seeded
    ):
        item = asked()

        response = review_client.post(
            f"/hitl/{item.id}/resolve", json={"choice": "SOMETHING_ELSE"}
        )

        assert response.status_code == 422

    def test_an_overtaken_card_is_a_conflict(
        self, review_client, asked, seeded, graph_store, sample_pattern, moment
    ):
        # Nothing about the request is wrong. It was valid when the card was
        # drawn and the world moved, which is what a conflict means.
        graph_store.mark_superseded(sample_pattern.node_id, at=moment)
        item = asked()

        response = review_client.post(
            f"/hitl/{item.id}/resolve", json={"choice": "APPROVE"}
        )

        assert response.status_code == 409

    def test_answering_twice_is_a_conflict(
        self, review_client, asked, seeded, make_proposal, make_item,
        sample_observation,
    ):
        item = asked(
            proposal=make_proposal(item=make_item(node_id=sample_observation.node_id))
        )
        review_client.post(f"/hitl/{item.id}/resolve", json={"choice": "APPROVE"})

        again = review_client.post(
            f"/hitl/{item.id}/resolve", json={"choice": "APPROVE"}
        )

        assert again.status_code == 409

    def test_answering_something_unknown_is_not_found(self, review_client):
        response = review_client.post(
            "/hitl/nothing/resolve", json={"choice": "APPROVE"}
        )

        assert response.status_code == 404


class TestDeferring:
    """Putting a question off."""

    def test_it_comes_back_with_the_dates_it_set(
        self, review_client, asked, seeded
    ):
        item = asked()

        body = review_client.post(f"/hitl/{item.id}/snooze").json()

        assert body["snooze_count"] == 1
        assert body["snoozed_until"] is not None
        assert body["auto_resolves_at"] is not None

    def test_the_item_stops_being_listed(self, review_client, asked, seeded):
        item = asked()

        review_client.post(f"/hitl/{item.id}/snooze")

        assert review_client.get("/hitl").json()["cards"] == []

    def test_deferring_something_unknown_is_not_found(self, review_client):
        assert review_client.post("/hitl/nothing/snooze").status_code == 404


class TestWithdrawing:
    """Taking a question nobody can answer off the list."""

    def test_an_unanswerable_item_can_be_withdrawn(
        self, review_client, asked, seeded
    ):
        item = asked(save_proposal=False)

        response = review_client.post(f"/hitl/{item.id}/dismiss")

        assert response.status_code == 200
        assert response.json()["writes_nothing"] is True

    def test_the_queue_empties(self, review_client, asked, seeded):
        item = asked(save_proposal=False)

        review_client.post(f"/hitl/{item.id}/dismiss")

        assert review_client.get("/hitl").json()["cards"] == []

    def test_an_answerable_item_is_refused(self, review_client, asked, seeded):
        item = asked()

        response = review_client.post(f"/hitl/{item.id}/dismiss")

        assert response.status_code == 400

    def test_withdrawing_something_unknown_is_not_found(self, review_client):
        assert review_client.post("/hitl/nothing/dismiss").status_code == 404


class TestTheHousekeepingRoute:
    """The one a scheduler will call."""

    def test_it_says_what_it_did(self, review_client, asked, seeded):
        parked = asked(status=HitlItemStatus.SUSPENDED_QUEUE_FULL)

        body = review_client.post("/hitl/sweep").json()

        assert parked.id in body["admitted"]
        assert body["ran_at"]

    def test_it_reports_an_empty_pass_plainly(self, review_client):
        body = review_client.post("/hitl/sweep").json()

        assert body["auto_resolved"] == []
        assert body["admitted"] == []


def test_the_routes_cannot_reach_the_graph_themselves():
    """
    The review routes hold something narrow, never a graph handle.

    If this file ever names the graph or the index, the separation the whole
    arrangement depends on has quietly gone.
    """
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "api" / "routes" / "hitl.py"
    ).read_text()

    assert "get_graph" not in source
    assert "vectors" not in source
    assert "write_node" not in source
