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

    def test_building_it_reads_no_files_of_its_own(self, tmp_path, monkeypatch):
        # create_app has no side effects on purpose. A .env read here would
        # mean a test could be pointed at a real database by a file sitting
        # in somebody's checkout, and the variables would outlive the test.
        read: list[object] = []
        monkeypatch.setattr("lumen.env.load_env", lambda *a, **k: read.append(1))

        create_app(AppConfig(graph=GraphConfig(db_path=str(tmp_path / "a"))))

        assert read == []

    def test_the_configured_entry_point_reads_one(self, monkeypatch):
        # And the entry point meant for actually running it does, because
        # otherwise nothing would.
        read: list[object] = []
        monkeypatch.setattr("lumen.api.main.load_env", lambda *a, **k: read.append(1))

        from lumen.api.main import create_configured_app

        create_configured_app()

        assert read == [1]

    def test_every_route_is_documented(self, api_client):
        # The documentation is generated from the routes themselves, so it
        # cannot drift from what the service actually does.
        spec = api_client.get("/openapi.json").json()

        assert "/graph/nodes" in spec["paths"]
        assert "/debug/traces/{trace_id}" in spec["paths"]


class TestTheTestPages:
    def test_they_are_served(self, api_client):
        response = api_client.get("/ui/")

        assert response.status_code == 200
        assert "Lumen" in response.text

    def test_all_three_are_there(self, api_client):
        for page in ("index.html", "trace.html", "chat.html"):
            assert api_client.get(f"/ui/{page}").status_code == 200, page

    def test_they_say_what_they_are(self, api_client):
        # These exist to make the pipeline visible while it is being built.
        # Somebody landing on one should not mistake it for the product.
        assert "not the product UI" in api_client.get("/ui/index.html").text

    def test_nothing_from_the_server_is_inserted_as_markup(self):
        # The strings on these pages are somebody's journal. A page that
        # rendered them as HTML would run whatever an export happened to
        # contain.
        static = Path(__file__).resolve().parents[1] / "api" / "static"
        offenders = [
            path.name
            for path in static.glob("*.js")
            if "innerHTML" in path.read_text()
        ]

        assert offenders == []

    def test_a_service_with_no_pages_still_starts(self, tmp_path, monkeypatch):
        # The service is an API first and works perfectly without a page.
        import lumen.api.main as module

        monkeypatch.setattr(
            module.Path, "is_dir", lambda self: False
        )
        app = create_app(AppConfig(graph=GraphConfig(db_path=str(tmp_path / "g"))))

        assert not [route for route in app.routes if getattr(route, "name", "") == "ui"]


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


class TestTheSchemaTheServiceStartsWith:
    """
    Starting up runs the migrations rather than creating tables.

    The difference is invisible on day one and decides everything after it.
    A database built by creating tables carries no record of which
    migrations it represents, so the next column added to a model never
    reaches it — and the failure surfaces much later as `no such column`
    from whichever query touches it first, with nothing pointing back at a
    schema that was never migrated. That happened here, on real data.
    """

    def _config(self, tmp_path) -> AppConfig:
        return AppConfig(
            graph=GraphConfig(db_path=str(tmp_path / "graph")),
            operational=OperationalConfig(db_url=f"sqlite:///{tmp_path / 'ops.db'}"),
        )

    def test_a_fresh_database_comes_up_fully_migrated(self, tmp_path):
        from fastapi.testclient import TestClient
        from sqlalchemy import create_engine, inspect

        from lumen.operational.migrator import detect_schema_drift

        config = self._config(tmp_path)
        with TestClient(create_app(config)):
            pass

        engine = create_engine(config.operational.db_url)
        try:
            assert "alembic_version" in set(inspect(engine).get_table_names())
            assert detect_schema_drift(engine) == []
        finally:
            engine.dispose()

    def test_a_database_with_tables_but_no_history_is_refused(self, tmp_path):
        """
        The state the old path left behind, and the one state that cannot be
        resolved by guessing: which migration those tables represent is a
        question for a person.
        """
        from fastapi.testclient import TestClient
        from sqlalchemy import create_engine

        from lumen.operational import models

        config = self._config(tmp_path)
        engine = create_engine(config.operational.db_url)
        models.Base.metadata.create_all(engine)
        engine.dispose()

        with pytest.raises(RuntimeError, match="no migration history"):
            with TestClient(create_app(config)):
                pass

    def test_the_refusal_says_how_to_get_out_of_it(self, tmp_path):
        from fastapi.testclient import TestClient
        from sqlalchemy import create_engine

        from lumen.operational import models

        config = self._config(tmp_path)
        engine = create_engine(config.operational.db_url)
        models.Base.metadata.create_all(engine)
        engine.dispose()

        with pytest.raises(RuntimeError) as raised:
            with TestClient(create_app(config)):
                pass

        assert "alembic stamp" in str(raised.value)
        assert "alembic upgrade head" in str(raised.value)


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

    def test_everything_touching_the_graph_is_a_get(self, api_client):
        # The verb is the promise callers read first, so every path that
        # reaches the graph or the run history keeps it.
        spec = api_client.get("/openapi.json").json()

        used = {
            verb.upper()
            for path, operations in spec["paths"].items()
            for verb in operations
            if path.startswith(("/graph", "/debug", "/health"))
        }
        assert used == {"GET"}

    def test_every_post_is_one_that_has_earned_it(self, api_client):
        # An allow-list rather than a count, so that adding another is a
        # deliberate act with a reason written next to it.
        #
        #   /query/formulate — changes nothing, but what it is given is
        #     somebody's sentence about their own life. A GET would put that
        #     in the URL, and from there into every access log it passes
        #     through.
        #
        #   /query/retrieve — the same sentence, and the same reason. It
        #     reads the graph and the search index and writes to neither;
        #     the only thing it changes is one in-memory conversation's
        #     memory of itself, which is gone at midnight and never stored.
        #
        #   /query/prompt — the same sentence again, and the same reason.
        #     It shows exactly what the assistant would be sent and writes
        #     nothing anywhere; it does not even generate a reply.
        #
        #   /ingest/file, /ingest/json — the one way in. Both hand what they
        #     receive to the importer and put an identifier on a queue;
        #     neither can reach the graph, which is what the two tests above
        #     check by type and by name.
        #
        #   /chat/transcribe — a recording of somebody's voice, which has
        #     even less business in a URL than a sentence does. It writes
        #     nothing; the words come back and are sent on as an ordinary
        #     turn.
        #
        #   /chat/messages/{message_id}/revise — the one POST here that
        #     really does change something, and what it changes is the
        #     conversation, never the graph. It writes the rewrite beside the
        #     original rather than over it, and refuses outright once the day
        #     has been processed.
        spec = api_client.get("/openapi.json").json()

        posts = {
            path
            for path, operations in spec["paths"].items()
            if "post" in operations
        }
        assert posts == {
            "/query/formulate",
            "/query/retrieve",
            "/query/prompt",
            "/ingest/file",
            "/ingest/json",
            "/chat/transcribe",
            "/chat/messages/{message_id}/revise",
        }

    def test_the_upload_routes_cannot_reach_the_graph_themselves(self):
        # The importer is the only thing in the process that writes, and the
        # routes that hold it can do exactly one thing with it: queue an
        # identifier. If this file ever names the graph, the separation the
        # whole arrangement depends on has quietly gone.
        source = (
            Path(__file__).resolve().parents[1] / "api" / "routes" / "ingest.py"
        ).read_text()

        assert "get_graph" not in source
        assert "vectors" not in source
        assert "run_pipeline" not in source
