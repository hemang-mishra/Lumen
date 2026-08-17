"""
The one way to see what the system makes of a sentence.

Everything this endpoint reports happens invisibly in the real product,
between somebody speaking and the AI answering. Without it the only evidence
of whether the reading is any good is a log line.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from lumen.api.deps import get_formulator, get_graph, get_ops
from lumen.api.main import create_app
from lumen.providers.fake import FakeLLMProvider
from lumen.query.deadline import DeadlineExceeded
from lumen.query.formulation import QueryFormulator


def reply(triggers=None, register="STABLE", entities=None) -> str:
    """The JSON a classifier would return."""
    return json.dumps(
        {
            "triggers": triggers or [],
            "named_entities": entities or [],
            "emotional_register": register,
            "confidence": 0.8,
            "critical_domain_opened": None,
        }
    )


@pytest.fixture
def query_client(graph_store, ops_store):
    """A client whose turn reader answers from a script."""

    def _build(script):
        formulator = QueryFormulator(
            llm=FakeLLMProvider(list(script)), graph=graph_store
        )
        app = create_app()
        app.dependency_overrides[get_graph] = lambda: graph_store
        app.dependency_overrides[get_ops] = lambda: ops_store
        app.dependency_overrides[get_formulator] = lambda: formulator
        app.state.graph = graph_store
        app.state.ops = ops_store
        app.state.formulator = formulator
        return TestClient(app, raise_server_exceptions=False), formulator

    built = []

    def _make(script=()):
        client, formulator = _build(script)
        built.append(formulator)
        return client

    yield _make

    for formulator in built:
        formulator.close()


class TestReadingASentence:
    def test_a_sentence_comes_back_with_what_would_be_looked_up(self, query_client):
        client = query_client([reply(triggers=[{"trigger_type": "SOMATIC_MARKER"}])])

        body = client.post(
            "/query/formulate",
            json={"text": "there is a tightness in my chest again"},
        ).json()

        assert body["retrieval_triggers"][0]["trigger_type"] == "SOMATIC_MARKER"
        assert body["formulation_path"] == "CLASSIFIED"

    def test_small_talk_comes_back_empty_without_a_model_call(self, query_client):
        client = query_client([])

        body = client.post("/query/formulate", json={"text": "thanks"}).json()

        assert body["retrieval_triggers"] == []
        assert body["formulation_path"] == "ACKNOWLEDGEMENT"

    def test_a_distress_phrase_comes_back_as_a_crisis(self, query_client):
        client = query_client([])

        body = client.post(
            "/query/formulate", json={"text": "some days I just want to die"}
        ).json()

        assert body["emotional_register"] == "CRISIS"
        assert body["formulation_path"] == "SAFETY_FLOOR"

    def test_earlier_messages_can_be_supplied_for_context(self, query_client):
        client = query_client([reply(triggers=[{"trigger_type": "PROGRESS_CLAIM"}])])

        response = client.post(
            "/query/formulate",
            json={
                "text": "I don't feel that anymore",
                "history": [
                    {"role": "user", "content": "I used to think I was the problem"},
                    {"role": "assistant", "content": "What changed?"},
                ],
            },
        )

        assert response.status_code == 200
        assert response.json()["turn_index"] == 2

    def test_a_real_person_in_the_graph_grounds(self, query_client, graph_store):
        graph_store.write_node(
            "PersonEntityNode",
            {
                "node_id": "person_alex",
                "canonical_name": "Alex",
                "first_mentioned_at": "2026-06-01T00:00:00+00:00",
                "last_mentioned_at": "2026-06-01T00:00:00+00:00",
                "mention_count": 1,
                "relationship_to_user": "FRIEND",
                "relationship_sentiment_trend": "STABLE",
                "is_canonical": True,
                "status": "ACTIVE",
                "aliases": "[]",
            },
        )
        client = query_client(
            [
                reply(
                    triggers=[{"trigger_type": "NAMED_PERSON", "people": ["Alex"]}],
                    entities=["Alex"],
                )
            ]
        )

        body = client.post(
            "/query/formulate", json={"text": "Alex said the same thing last year"}
        ).json()

        assert body["retrieval_triggers"][0]["person_node_ids"] == ["person_alex"]

    def test_nothing_is_carried_between_two_calls(self, query_client):
        # This surface exists to look at what a sentence is made of, not to
        # hold a conversation. Two callers must never see each other's turns.
        client = query_client([reply(), reply()])

        first = client.post("/query/formulate", json={"text": "first thing"}).json()
        second = client.post("/query/formulate", json={"text": "second thing"}).json()

        assert first["turn_index"] == 0
        assert second["turn_index"] == 0


class TestRefusingBadRequests:
    def test_an_empty_sentence_is_refused(self, query_client):
        client = query_client([])

        assert client.post("/query/formulate", json={"text": ""}).status_code == 422

    def test_a_missing_sentence_is_refused(self, query_client):
        client = query_client([])

        assert client.post("/query/formulate", json={}).status_code == 422

    def test_fields_nobody_asked_for_are_refused(self, query_client):
        client = query_client([])

        response = client.post(
            "/query/formulate", json={"text": "hello", "user_id": "somebody else"}
        )

        assert response.status_code == 422

    def test_an_unreasonable_amount_of_history_is_refused(self, query_client):
        client = query_client([])

        response = client.post(
            "/query/formulate",
            json={
                "text": "hello",
                "history": [{"content": f"turn {i}"} for i in range(50)],
            },
        )

        assert response.status_code == 422


class TestWhenNoModelIsConfigured:
    """
    A service started with no model reachable at all.

    These run the real startup rather than setting the state by hand,
    because what is being checked is that starting up survives it.
    """

    @pytest.fixture
    def unconfigured_client(self, tmp_path, monkeypatch):
        from lumen.config import AppConfig, GraphConfig, OperationalConfig

        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        app = create_app(
            AppConfig(
                graph=GraphConfig(db_path=str(tmp_path / "graph")),
                operational=OperationalConfig(
                    db_url=f"sqlite:///{tmp_path / 'ops.db'}"
                ),
            )
        )

        with TestClient(app, raise_server_exceptions=False) as client:
            assert app.state.formulator is None
            yield client

    def test_it_says_so_plainly_rather_than_failing(self, unconfigured_client):
        # The fix is to configure a model, and a generic apology would send
        # somebody looking for a bug instead.
        response = unconfigured_client.post("/query/formulate", json={"text": "hi"})

        assert response.status_code == 503
        assert response.json()["error"] == "unavailable"
        assert "no language model is configured" in response.json()["detail"]

    def test_the_rest_of_the_service_keeps_working(self, unconfigured_client):
        # Everything else here reads two local databases and needs no model
        # at all, so one missing piece must not take the whole thing down.
        assert unconfigured_client.get("/graph/stats").status_code == 200
        assert unconfigured_client.get("/health").json()["status"] == "ok"


class TestBuildingTheTurnReaderAtStartup:
    def test_a_missing_credential_does_not_stop_the_service_starting(
        self, monkeypatch, graph_store
    ):
        # Every other thing this service does reads two local databases and
        # works perfectly without a model, so a missing key must confine
        # itself to the one surface that needs one.
        from lumen.api.main import _build_formulator
        from lumen.config import AppConfig

        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

        assert _build_formulator(AppConfig(), graph_store) is None

    def test_a_configured_model_is_built_without_retries(
        self, monkeypatch, graph_store
    ):
        # This one call has a deadline in fractions of a second. A retry has
        # already missed it, and only guarantees the wait is spent twice.
        from lumen.api.main import _build_formulator
        from lumen.config import AppConfig, ProviderConfig

        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        config = AppConfig(providers=ProviderConfig(lightweight_provider="fake"))

        formulator = _build_formulator(config, graph_store)
        try:
            assert formulator is not None
            assert formulator._llm._config.max_attempts == 1
        finally:
            formulator.close()

    def test_the_reader_is_opened_and_closed_with_the_service(
        self, tmp_path, monkeypatch
    ):
        from lumen.config import (
            AppConfig,
            GraphConfig,
            OperationalConfig,
            ProviderConfig,
        )

        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        config = AppConfig(
            graph=GraphConfig(db_path=str(tmp_path / "graph")),
            operational=OperationalConfig(db_url=f"sqlite:///{tmp_path / 'ops.db'}"),
            providers=ProviderConfig(lightweight_provider="fake"),
        )
        app = create_app(config)

        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
            reader = app.state.formulator
            assert reader is not None

        # The pool holds threads, so leaving it running would outlive the
        # service that opened it.
        with pytest.raises(DeadlineExceeded):
            reader._runner.run(lambda: 1, timeout_seconds=1)


class TestHandingTheReaderToARequest:
    def test_a_request_is_given_the_one_the_service_opened(self, graph_store):
        # Built once at startup because it holds a model connection and a
        # pool of threads; building one per request would pay for both on
        # every call.
        from types import SimpleNamespace

        from lumen.api.deps import get_formulator

        reader = QueryFormulator(llm=FakeLLMProvider([]), graph=graph_store)
        try:
            request = SimpleNamespace(
                app=SimpleNamespace(state=SimpleNamespace(formulator=reader))
            )

            assert get_formulator(request) is reader
        finally:
            reader.close()
