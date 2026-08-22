"""
Tests for the one way into Lumen.

The worker is stood in for throughout. What these check is the web layer's
half of the job — that a file is read, stored, and acknowledged with
something the caller can follow, and that everything which can go wrong with
an upload comes back as an answer somebody can act on rather than as a
generic apology. Whether the pipeline then does the right thing is settled
in the worker's own tests, against a real run.

The one thing checked here that is not about HTTP is the order of
operations: nothing is written until a model is known to be reachable.
"""

from __future__ import annotations

import io
import json
from datetime import UTC, date, datetime

import pytest

from lumen.api.deps import get_config, get_ops, get_worker
from lumen.api.main import create_app
from lumen.config import AppConfig, IngestConfig
from lumen.operational.enums import ImportStatus
from lumen.operational.schemas import ImportRecord
from lumen.providers.errors import ProviderError

EXPORT = {
    "id": "6a6f18ef-3088-83e8-b4fe-caf926cc356d",
    "title": "Aug 2",
    "lastUpdated": "2026-08-02T14:10:59.170Z",
    "messages": [
        {
            "id": "m1",
            "role": "user",
            "content": "The morning got away from me again.",
            "timestamp": "2026-08-02T10:16:15.611Z",
        },
        {
            "id": "m2",
            "role": "assistant",
            "content": "What were you avoiding?",
            "timestamp": "2026-08-02T10:16:16.031Z",
        },
    ],
}


class FakeWorker:
    """
    A worker that records what it was asked to do and does none of it.

    Standing the real one in would mean a model, a vector store and several
    minutes per test, none of which says anything about whether the route
    behaved.
    """

    def __init__(self, *, ready: bool = True) -> None:
        self.ready = ready
        self.submitted: list[str] = []

    def ensure_ready(self):
        if not self.ready:
            raise ProviderError("no credential for gemini")
        return object()

    def submit(self, import_id: str) -> None:
        self.submitted.append(import_id)


@pytest.fixture
def worker() -> FakeWorker:
    return FakeWorker()


@pytest.fixture
def ingest_client(graph_store, ops_store, worker):
    """A client for the API with a stand-in importer behind the uploads."""
    from fastapi.testclient import TestClient

    config = AppConfig()
    app = create_app(config)
    app.dependency_overrides[get_ops] = lambda: ops_store
    app.dependency_overrides[get_worker] = lambda: worker
    app.dependency_overrides[get_config] = lambda: config
    app.state.graph = graph_store
    app.state.ops = ops_store
    app.state.ingest = worker

    return TestClient(app, raise_server_exceptions=False)


def upload(client, payload=EXPORT, filename: str = "aug2.json"):
    """Send an export the way a browser does."""
    return client.post(
        "/ingest/file",
        files={"file": (filename, io.BytesIO(json.dumps(payload).encode()), "application/json")},
    )


class TestUploadingAFile:
    def test_it_is_accepted_rather_than_awaited(self, ingest_client):
        # One conversation is several model calls and takes minutes. The
        # answer is a receipt, handed over before the work starts.
        response = upload(ingest_client)

        assert response.status_code == 202

    def test_the_receipt_names_something_worth_following(self, ingest_client):
        body = upload(ingest_client).json()

        assert body["batch_id"]
        assert body["queued"] == 1
        assert body["conversations"][0]["import_id"]
        assert body["conversations"][0]["session_id"]

    def test_the_receipt_reports_the_day_it_worked_out(self, ingest_client):
        # The whole point of the feature. If this is wrong, everything the
        # pipeline builds is filed under the wrong day.
        body = upload(ingest_client).json()

        assert body["conversations"][0]["event_date"] == "2026-08-02"

    def test_the_conversation_is_queued(self, ingest_client, worker):
        body = upload(ingest_client).json()

        assert worker.submitted == [body["conversations"][0]["import_id"]]

    def test_the_messages_are_stored_before_the_answer_comes_back(
        self, ingest_client, ops_store
    ):
        body = upload(ingest_client).json()

        session_id = body["conversations"][0]["session_id"]
        assert len(ops_store.buffers.get_messages(session_id)) == 2

    def test_the_file_it_came_from_is_remembered(self, ingest_client, ops_store):
        upload(ingest_client, filename="my-export.json")

        assert ops_store.imports.list_recent("local")[0].filename == "my-export.json"


class TestSendingItAsJson:
    def test_the_direct_route_does_the_same_thing(self, ingest_client, worker):
        response = ingest_client.post("/ingest/json", json=EXPORT)

        assert response.status_code == 202
        assert len(worker.submitted) == 1

    def test_a_name_can_be_given_for_the_history(self, ingest_client, ops_store):
        ingest_client.post("/ingest/json?filename=nightly-sync", json=EXPORT)

        assert ops_store.imports.list_recent("local")[0].filename == "nightly-sync"

    def test_a_list_of_conversations_is_accepted(self, ingest_client, worker):
        second = {**EXPORT, "id": "second", "title": "Aug 3"}
        response = ingest_client.post("/ingest/json", json=[EXPORT, second])

        assert response.json()["queued"] == 2
        assert len(worker.submitted) == 2


class TestWhenTheUploadIsNoGood:
    def test_a_file_that_is_not_json_says_so(self, ingest_client):
        response = ingest_client.post(
            "/ingest/file",
            files={"file": ("notes.json", io.BytesIO(b"this is not json"), "application/json")},
        )

        assert response.status_code == 400
        assert "not valid JSON" in response.json()["detail"]

    def test_an_empty_file_says_so(self, ingest_client):
        response = ingest_client.post(
            "/ingest/file",
            files={"file": ("empty.json", io.BytesIO(b"   "), "application/json")},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "that file is empty"

    def test_a_file_that_is_not_text_says_so(self, ingest_client):
        response = ingest_client.post(
            "/ingest/file",
            files={"file": ("photo.json", io.BytesIO(b"\xff\xfe\x00\x01binary"), "application/json")},
        )

        assert response.status_code == 400
        assert "not text" in response.json()["detail"]

    def test_an_absurdly_large_file_is_refused_before_being_read(
        self, ingest_client, monkeypatch
    ):
        # Refused on size rather than parsed and then found to be nonsense.
        # A chat export is text; anything near the limit is a mistake, and
        # decoding it first costs the memory the mistake was going to cost.
        monkeypatch.setattr("lumen.api.routes.ingest.MAX_UPLOAD_BYTES", 1_048_576)
        oversized = io.BytesIO(b"x" * (2 * 1_048_576))

        response = ingest_client.post(
            "/ingest/file",
            files={"file": ("huge.json", oversized, "application/json")},
        )

        assert response.status_code == 400
        assert "the limit is 1 MB" in response.json()["detail"]

    def test_json_that_is_not_an_export_says_what_was_wrong(self, ingest_client):
        response = ingest_client.post("/ingest/json", json={"hello": "world"})

        assert response.status_code == 400
        assert "no messages" in response.json()["detail"]

    def test_nothing_is_stored_when_the_file_cannot_be_read(
        self, ingest_client, ops_store
    ):
        ingest_client.post("/ingest/json", json={"hello": "world"})

        assert ops_store.imports.list_recent("local") == []

    def test_a_conversation_that_cannot_be_read_is_named_not_swallowed(
        self, ingest_client
    ):
        # Twenty-eight of thirty conversations importing is the right
        # outcome; being told which two were dropped is the rest of it.
        hollow = {"id": "hollow", "title": "Empty", "messages": []}
        body = ingest_client.post("/ingest/json", json=[EXPORT, hollow]).json()

        assert body["queued"] == 1
        assert body["rejected"] == [
            {
                "source_conversation_id": "hollow",
                "title": "Empty",
                "reason": "it has no messages",
            }
        ]


class TestNothingIsWrittenWithoutAModel:
    def test_an_upload_is_refused_when_no_model_is_configured(self, ingest_client, worker):
        worker.ready = False

        response = upload(ingest_client)

        assert response.status_code == 503
        assert "no usable model" in response.json()["detail"]

    def test_the_refusal_happens_before_anything_is_stored(
        self, ingest_client, ops_store, worker
    ):
        # Otherwise the file is accepted, written, and reported as failed
        # four minutes later — which reads as a problem with the export.
        worker.ready = False

        upload(ingest_client)

        assert ops_store.imports.list_recent("local") == []
        assert ops_store.buffers.find_decayed(datetime.now(UTC)) == []


class TestUploadingTheSameFileTwice:
    def test_the_second_time_queues_nothing(self, ingest_client, worker):
        upload(ingest_client)
        worker.submitted.clear()

        body = upload(ingest_client).json()

        assert body["queued"] == 0
        assert worker.submitted == []

    def test_the_second_time_says_it_has_seen_this_before(self, ingest_client):
        first = upload(ingest_client).json()

        again = upload(ingest_client).json()

        assert again["conversations"][0]["already_imported"] is True
        assert again["conversations"][0]["import_id"] == first["conversations"][0]["import_id"]


class TestFollowingAnUpload:
    def test_an_upload_can_be_polled_by_its_batch(self, ingest_client):
        batch_id = upload(ingest_client).json()["batch_id"]

        body = ingest_client.get(f"/ingest/imports/{batch_id}").json()

        assert body["batch_id"] == batch_id
        assert body["filename"] == "aug2.json"
        assert len(body["imports"]) == 1

    def test_an_upload_still_running_is_not_finished(self, ingest_client):
        batch_id = upload(ingest_client).json()["batch_id"]

        assert ingest_client.get(f"/ingest/imports/{batch_id}").json()["finished"] is False

    def test_an_upload_whose_work_is_done_says_so(self, ingest_client, ops_store):
        body = upload(ingest_client).json()
        ops_store.imports.update_status(
            body["conversations"][0]["import_id"],
            ImportStatus.COMPLETE,
            trace_id="trace_abc",
        )

        polled = ingest_client.get(f"/ingest/imports/{body['batch_id']}").json()

        assert polled["finished"] is True
        assert polled["imports"][0]["trace_id"] == "trace_abc"

    def test_an_upload_nobody_made_says_which_one_was_asked_for(self, ingest_client):
        response = ingest_client.get("/ingest/imports/batch_nobody_made")

        assert response.status_code == 404
        assert response.json()["id"] == "batch_nobody_made"


class TestTheHistory:
    def test_past_imports_are_listed(self, ingest_client):
        upload(ingest_client)

        body = ingest_client.get("/ingest/imports").json()

        assert len(body) == 1
        assert body[0]["title"] == "Aug 2"
        assert body[0]["status"] == "QUEUED"

    def test_the_newest_comes_first(self, ingest_client, ops_store):
        upload(ingest_client)
        ops_store.imports.record(
            ImportRecord(
                import_id="imp_older",
                batch_id="batch_older",
                user_id="local",
                source_conversation_id="conv-older",
                title="Older",
                event_date=date(2026, 7, 1),
            )
        )

        titles = [item["title"] for item in ingest_client.get("/ingest/imports").json()]
        assert titles[0] == "Older"

    def test_the_history_can_be_kept_short(self, ingest_client):
        upload(ingest_client)

        assert len(ingest_client.get("/ingest/imports?limit=1").json()) == 1

    def test_an_absurd_limit_is_refused(self, ingest_client):
        assert ingest_client.get("/ingest/imports?limit=100000").status_code == 422


class TestTurningUploadsOff:
    def test_the_routes_are_not_there_at_all(self, tmp_path):
        # Not mounted and refusing — absent. A deployment that says it only
        # reads should not have a write endpoint in its documentation.
        from fastapi.testclient import TestClient

        app = create_app(AppConfig(ingest=IngestConfig(enabled=False)))
        client = TestClient(app, raise_server_exceptions=False)

        spec = client.get("/openapi.json").json()
        assert not [path for path in spec["paths"] if path.startswith("/ingest")]

    def test_no_importer_is_opened_at_all(self, tmp_path):
        # The switch is not just about routing. A deployment that refuses
        # uploads should not have a thread sitting waiting for one.
        from fastapi.testclient import TestClient

        from lumen.config import GraphConfig, OperationalConfig

        app = create_app(
            AppConfig(
                ingest=IngestConfig(enabled=False),
                graph=GraphConfig(db_root=str(tmp_path / "graph")),
                operational=OperationalConfig(db_url=f"sqlite:///{tmp_path / 'ops.db'}"),
            )
        )

        with TestClient(app):
            assert app.state.ingest is None

    def test_a_request_is_handed_the_importer_the_service_opened(
        self, graph_store, worker
    ):
        from fastapi import Request

        from lumen.api.deps import get_worker

        app = create_app(AppConfig())
        app.state.ingest = worker
        request = Request({"type": "http", "app": app, "headers": []})

        assert get_worker(request) is worker

    def test_a_request_reaching_the_importer_without_one_says_so(
        self, graph_store, ops_store
    ):
        # Cannot happen through the routes, which were never mounted. It is
        # still answered, because "the router was not included" is a fact
        # about startup and this is a fact about a request.
        from fastapi import Request

        from lumen.api.deps import get_worker
        from lumen.api.errors import Unavailable

        app = create_app(AppConfig())
        app.state.ingest = None
        request = Request({"type": "http", "app": app, "headers": []})

        with pytest.raises(Unavailable, match="does not accept uploads"):
            get_worker(request)

    def test_the_rest_of_the_api_is_unaffected(
        self, graph_store, vector_store, ops_store
    ):
        from fastapi.testclient import TestClient

        from lumen.tests.conftest import registry_for

        app = create_app(AppConfig(ingest=IngestConfig(enabled=False)))
        app.dependency_overrides[get_ops] = lambda: ops_store
        app.state.stores = registry_for(graph_store, vector_store)
        app.state.ops = ops_store
        client = TestClient(app, raise_server_exceptions=False)

        assert client.get("/graph/stats").status_code == 200
