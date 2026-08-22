"""
Tests for the maintenance routes.

Most of this is about the one operation in Lumen that cannot be undone.
Everything an irreversible thing needs is checked here: that looking changes
nothing, that a request without the word this deployment asks for is refused,
that a refusal a caller can fix reads differently from one they can only wait
out, and that nothing leaks a stack trace on the way back.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from lumen.tests.conftest import registry_for

from lumen.config import AppConfig
from lumen.erasure.contracts import ErasureRefused
from lumen.erasure.service import ErasureService
from lumen.graph.queries import tidy_row

NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)


@pytest.fixture
def maintenance_client(api_client, graph_store, vector_store, ops_store):
    """The API, with erasure wired to the same test databases."""
    from lumen.api.deps import get_eraser

    service = ErasureService(
        config=AppConfig(),
        stores=registry_for(graph_store, vector_store),
        ops=ops_store,
    )
    api_client.app.dependency_overrides[get_eraser] = lambda: service
    api_client.app.state.eraser = service
    return api_client


@pytest.fixture
def one_evening(graph_store):
    """One piece of writing and what was read out of it."""
    graph_store.write_node(
        "EpisodeNode",
        {
            "node_id": "ep_1",
            "entry_id": "sess_1",
            "occurred_at": NOW.isoformat(),
            "created_at": NOW.isoformat(),
            "valid_from": NOW.isoformat(),
            "event_date": date(2026, 8, 20),
            "session_label": "evening",
            "source_modality": "TEXT_ENTRY",
            "entry_class": "REFLECTIVE",
            "episode_summary": "a hard evening",
            "episode_index": 1,
            "total_episodes_in_entry": 1,
            "reconciliation_status": "COMPLETE",
            "raw_text_hash": "hash",
        },
    )
    return "sess_1"


class TestLookingBeforeAgreeing:
    def test_a_preview_counts_what_would_go(self, maintenance_client, one_evening):
        answer = maintenance_client.get(
            "/maintenance/erasure/preview",
            params={"scope": "ENTRY", "entry_id": one_evening},
        )

        assert answer.status_code == 200
        assert answer.json()["total_records"] == 1

    def test_a_preview_changes_nothing(self, maintenance_client, graph_store, one_evening):
        maintenance_client.get(
            "/maintenance/erasure/preview",
            params={"scope": "ENTRY", "entry_id": one_evening},
        )

        assert tidy_row(graph_store.get_node("ep_1"))["episode_summary"] == "a hard evening"

    def test_a_preview_needs_no_confirmation(self, maintenance_client, one_evening):
        # Somebody deciding whether to go ahead should not have to type the
        # word that means yes to find out what yes would mean.
        answer = maintenance_client.get(
            "/maintenance/erasure/preview", params={"scope": "ALL"}
        )

        assert answer.status_code == 200

    def test_it_says_what_it_will_not_reach(self, maintenance_client, one_evening):
        answer = maintenance_client.get(
            "/maintenance/erasure/preview",
            params={"scope": "ENTRY", "entry_id": one_evening},
        )

        assert answer.json()["not_reached"]

    def test_an_entry_sized_preview_with_no_entry_is_refused(self, maintenance_client):
        answer = maintenance_client.get(
            "/maintenance/erasure/preview", params={"scope": "ENTRY"}
        )

        assert answer.status_code == 400


class TestAskingForOne:
    def test_it_runs_and_reports_what_it_did(self, maintenance_client, one_evening):
        answer = maintenance_client.post(
            "/maintenance/erasure",
            json={"scope": "ENTRY", "entry_id": one_evening, "confirmation": "ERASE"},
        )

        assert answer.status_code == 200
        assert answer.json()["status"] == "COMPLETE"
        assert answer.json()["records_anonymized"] == 1

    def test_the_wrong_word_is_refused(self, maintenance_client, one_evening):
        answer = maintenance_client.post(
            "/maintenance/erasure",
            json={"scope": "ENTRY", "entry_id": one_evening, "confirmation": "yes"},
        )

        assert answer.status_code == 400

    def test_the_wrong_word_erases_nothing(self, maintenance_client, graph_store, one_evening):
        maintenance_client.post(
            "/maintenance/erasure",
            json={"scope": "ENTRY", "entry_id": one_evening, "confirmation": "yes"},
        )

        assert tidy_row(graph_store.get_node("ep_1"))["episode_summary"] == "a hard evening"

    def test_an_entry_nobody_wrote_is_refused(self, maintenance_client):
        answer = maintenance_client.post(
            "/maintenance/erasure",
            json={"scope": "ENTRY", "entry_id": "sess_never", "confirmation": "ERASE"},
        )

        assert answer.status_code == 400

    def test_one_already_running_is_a_different_answer(
        self, maintenance_client, one_evening
    ):
        # Not a wrong request — the world is simply busy, and a caller that
        # waits and repeats it will succeed.
        from lumen.api.deps import get_eraser

        class Busy:
            def erase(self, request, *, at=None):
                raise ErasureRefused("an erasure is already running; wait for it")

        maintenance_client.app.dependency_overrides[get_eraser] = lambda: Busy()

        answer = maintenance_client.post(
            "/maintenance/erasure",
            json={"scope": "ENTRY", "entry_id": one_evening, "confirmation": "ERASE"},
        )

        assert answer.status_code == 409

    def test_naming_an_entry_while_asking_for_everything_is_refused(
        self, maintenance_client, one_evening
    ):
        answer = maintenance_client.post(
            "/maintenance/erasure",
            json={"scope": "ALL", "entry_id": one_evening, "confirmation": "ERASE"},
        )

        assert answer.status_code == 400


class TestTheReceipts:
    def test_they_start_empty(self, maintenance_client):
        answer = maintenance_client.get("/maintenance/erasure/audits")

        assert answer.status_code == 200
        assert answer.json() == {"audits": [], "count": 0}

    def test_one_appears_after_an_erasure(self, maintenance_client, one_evening):
        maintenance_client.post(
            "/maintenance/erasure",
            json={"scope": "ENTRY", "entry_id": one_evening, "confirmation": "ERASE"},
        )

        listing = maintenance_client.get("/maintenance/erasure/audits").json()

        assert listing["count"] == 1
        assert listing["audits"][0]["status"] == "COMPLETE"

    def test_a_receipt_carries_a_hash_rather_than_a_name(
        self, maintenance_client, one_evening
    ):
        maintenance_client.post(
            "/maintenance/erasure",
            json={"scope": "ENTRY", "entry_id": one_evening, "confirmation": "ERASE"},
        )

        audit = maintenance_client.get("/maintenance/erasure/audits").json()["audits"][0]

        assert audit["user_id_hash"]
        assert "user_id" not in audit


class TestExplainingARanking:
    def test_the_four_parts_come_back_separately(self, maintenance_client, seed_pattern):
        # Once multiplied, the reason a record placed where it did is gone.
        seed_pattern("pat_1", valid_from="2024-01-01T00:00:00+00:00", signal="CRITICAL")

        answer = maintenance_client.get("/maintenance/score/pat_1").json()

        assert answer["signal_weight"] == 2.0
        assert answer["recency_weight"] == 0.5
        assert answer["trust_weight"] == 1.0
        assert answer["frequency_weight"] == 1.0
        assert answer["age_band"] == "DORMANT"

    def test_the_total_is_the_parts_multiplied(self, maintenance_client, seed_pattern):
        seed_pattern("pat_1", valid_from="2024-01-01T00:00:00+00:00")

        answer = maintenance_client.get("/maintenance/score/pat_1").json()

        assert answer["multiplier"] == pytest.approx(
            answer["signal_weight"]
            * answer["recency_weight"]
            * answer["trust_weight"]
            * answer["frequency_weight"]
        )

    def test_a_record_nobody_has_is_not_found(self, maintenance_client):
        assert maintenance_client.get("/maintenance/score/nope").status_code == 404


class TestTheWholeHistoryScan:
    def test_it_answers_nothing_on_an_empty_graph(self, maintenance_client):
        answer = maintenance_client.post("/maintenance/proof-chains")

        assert answer.status_code == 200
        assert answer.json() == {"chains": [], "count": 0}
