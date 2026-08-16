"""
Tests for the application itself, rather than for any one route.

The one that matters most is the last: the web layer is handed something
that cannot write, and a test says so. Every future goal with a "just let me
fix this one node" moment will find working routing and tests already in
place, and the shortest path from there is one more endpoint. That is
exactly how an append-only history stops being append-only.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lumen.api.main import create_app
from lumen.config import AppConfig, GraphConfig, OperationalConfig


class TestBuildingTheApplication:
    def test_it_is_built_by_a_function_not_on_import(self):
        # A module-level application would open a database the moment
        # anything named it, which makes it impossible to point a test at a
        # temporary one.
        import lumen.api.main as module

        assert not hasattr(module, "app")
        assert callable(module.create_app)

    def test_two_can_exist_with_different_settings(self, tmp_path):
        first = create_app(AppConfig(graph=GraphConfig(db_path=str(tmp_path / "a"))))
        second = create_app(AppConfig(graph=GraphConfig(db_path=str(tmp_path / "b"))))

        assert first.state.config.graph.db_path != second.state.config.graph.db_path

    def test_every_route_is_documented(self, api_client):
        # The documentation is generated from the routes themselves, so it
        # cannot drift from what the service actually does.
        spec = api_client.get("/openapi.json").json()

        assert "/graph/nodes" in spec["paths"]
        assert "/debug/traces/{trace_id}" in spec["paths"]


class TestStartingAndStopping:
    def test_both_stores_are_opened_and_closed(self, tmp_path):
        from fastapi.testclient import TestClient

        config = AppConfig(
            graph=GraphConfig(db_path=str(tmp_path / "graph")),
            operational=OperationalConfig(db_url=f"sqlite:///{tmp_path / 'ops.db'}"),
        )
        app = create_app(config)

        with TestClient(app) as client:
            assert client.get("/health").json()["status"] == "ok"
            provider = app.state.graph
            assert Path(config.graph.db_path).exists()

        # The graph is an embedded database holding a file lock. Leaving it
        # open would collide with a pipeline run started afterwards.
        assert provider.conn is None


class TestHealth:
    def test_a_working_service_says_so(self, api_client):
        body = api_client.get("/health").json()

        assert body == {"status": "ok", "graph": True, "operational": True}

    def test_a_store_that_cannot_answer_is_reported_separately(
        self, api_client, monkeypatch
    ):
        # A service that is running but cannot reach its databases is a
        # different problem from one that is down, and they are fixed
        # differently.
        def refuse():
            raise RuntimeError("the database is gone")

        monkeypatch.setattr(api_client.app.state.graph, "count_by_type", refuse)

        body = api_client.get("/health").json()

        assert body["status"] == "degraded"
        assert body["graph"] is False
        assert body["operational"] is True


class TestWhenSomethingGoesWrong:
    def test_an_unexpected_failure_does_not_leak_what_happened(
        self, api_client, monkeypatch
    ):
        # A stack trace or a database error handed back leaks the shape of
        # the store and, in a system holding somebody's private history,
        # sometimes a piece of what it holds.
        def explode():
            raise RuntimeError("secret detail about the storage layer")

        monkeypatch.setattr(api_client.app.state.graph, "count_by_type", explode)

        response = api_client.get("/graph/stats")

        assert response.status_code == 500
        assert "secret detail" not in response.text
        assert response.json()["error"] == "internal_error"

    def test_asking_for_something_missing_says_which_thing(self, api_client):
        body = api_client.get("/graph/nodes/no_such_node").json()

        assert body["kind"] == "node"
        assert body["id"] == "no_such_node"


class TestTheApiCannotWrite:
    def test_it_is_handed_a_reader_rather_than_the_full_store(self):
        # Not a convention: the write methods are not on the type the routes
        # are given, so a write endpoint would fail before it ran.
        from lumen.graph.provider import ReadOnlyGraph

        writes = {
            "write_node",
            "write_edge",
            "mark_superseded",
            "record_reinforcement",
            "touch_person",
            "transaction",
        }

        assert writes.isdisjoint(set(dir(ReadOnlyGraph)))

    @pytest.mark.parametrize(
        "method", ["write_node", "write_edge", "mark_superseded", "record_reinforcement"]
    )
    def test_no_route_names_a_write(self, method):
        package = Path(__file__).resolve().parents[1] / "api"
        offenders = [
            path.name for path in package.rglob("*.py") if method in path.read_text()
        ]

        assert offenders == []

    def test_nothing_but_reads_is_exposed(self, api_client):
        spec = api_client.get("/openapi.json").json()

        used = {
            verb.upper()
            for operations in spec["paths"].values()
            for verb in operations
        }
        assert used == {"GET"}
