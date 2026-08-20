"""
Tests for running imported conversations through the pipeline.

The happy path here is a real run: real stores, the shipped orchestrator,
and stand-ins only where a language model would otherwise be. That is the
only arrangement in which "the import finished" means anything — a fake
pipeline asked whether it succeeded will always say yes.

The rest of the file is about the promise that keeps the service usable:
nothing an import does may take the worker down with it.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, date, datetime

import pytest

from lumen.config import AppConfig
from lumen.ingest.contracts import ImportPlan, ParsedConversation, ParsedMessage
from lumen.ingest.loader import stage_conversations
from lumen.ingest.worker import IngestResources, IngestWorker
from lumen.operational.enums import ImportStatus
from lumen.operational.schemas import ImportRecord
from lumen.providers.errors import ProviderError

AUG_2 = date(2026, 8, 2)

# The conversation the fake models are scripted against. What it says does
# not matter — the stand-ins answer by which stage is asking — but it has to
# be a real back-and-forth, because that is what an export contains and it
# takes a different path through cleaning than a pasted monologue does.
ENTRY = (
    "I went to the cafe alone today and ate there without the usual dread. "
    "Then I saw what Alex had shipped this week and felt small and behind. "
    "I sat with it for a while and the pressure lifted on its own. "
    "I think the comparing is the thing that hurts, not the gap itself."
)


@pytest.fixture
def imported_conversation(ops_store):
    """One staged conversation, exactly as an upload would leave it."""

    def _stage(**overrides):
        conversation = ParsedConversation(
            source_conversation_id=overrides.pop("source_conversation_id", "conv-aug-2"),
            title="Aug 2",
            event_date=AUG_2,
            messages=[
                ParsedMessage(
                    message_id="m1",
                    role="USER",
                    content=ENTRY,
                    timestamp=datetime(2026, 8, 2, 10, 16, tzinfo=UTC),
                ),
                ParsedMessage(
                    message_id="m2",
                    role="AI",
                    content="What were you avoiding?",
                    timestamp=datetime(2026, 8, 2, 10, 17, tzinfo=UTC),
                ),
            ],
            **overrides,
        )
        return stage_conversations(
            ImportPlan(filename="aug2.json", conversations=[conversation]),
            ops=ops_store,
            user_id="local",
        )[0]

    return _stage


@pytest.fixture
def scripted_worker(ops_store, graph_store, vector_store, embedder, full_run_providers):
    """
    A worker wired to real stores and stand-in models.

    The conversation pass is scripted alongside the rest because an import
    is a dialogue, and a dialogue is read before it is cleaned.
    """

    def _build(overrides: dict[str, str] | None = None, **resource_overrides):
        light, deep = full_run_providers(
            {
                "conversation": json.dumps(
                    {
                        "turns": [
                            {
                                "message_id": "m1",
                                "dialogue_act": "EMOTIONAL_EXPRESSION",
                                "co_created_marker": False,
                            }
                        ],
                        "session_summary": ENTRY,
                        "co_created_spans": [],
                    }
                ),
                **(overrides or {}),
            }
        )
        resources = IngestResources(
            graph=resource_overrides.get("graph", graph_store),
            vectors=vector_store,
            embedder=embedder,
            lightweight=light,
            thinking=deep,
        )
        return IngestWorker(
            config=AppConfig(),
            ops=ops_store,
            graph=graph_store,
            resources=resources,
        )

    return _build


class TestOneWholeImport:
    def test_a_staged_conversation_reaches_the_graph(
        self, ops_store, graph_store, scripted_worker, imported_conversation
    ):
        staged = imported_conversation()

        scripted_worker().run_once(staged.import_id)

        record = ops_store.imports.get(staged.import_id)
        assert record.status is ImportStatus.COMPLETE
        assert sum(graph_store.count_by_type().values()) > 0

    def test_the_import_is_left_pointing_at_the_run_that_did_it(
        self, ops_store, scripted_worker, imported_conversation
    ):
        # This is the whole reason the import table exists. Without it an
        # upload finishes and there is no way back to what it wrote.
        staged = imported_conversation()

        scripted_worker().run_once(staged.import_id)

        record = ops_store.imports.get(staged.import_id)
        assert record.job_id
        assert record.trace_id
        assert ops_store.jobs.get_trace(record.trace_id) is not None

    def test_the_trace_it_points_at_holds_what_the_run_wrote(
        self, ops_store, scripted_worker, imported_conversation
    ):
        staged = imported_conversation()

        scripted_worker().run_once(staged.import_id)

        trace = ops_store.jobs.get_trace(ops_store.imports.get(staged.import_id).trace_id)
        assert trace.stage_runs != []
        assert trace.writes != []

    def test_a_finished_import_is_stamped_with_the_time(
        self, ops_store, scripted_worker, imported_conversation
    ):
        staged = imported_conversation()

        scripted_worker().run_once(staged.import_id)

        assert ops_store.imports.get(staged.import_id).finished_at is not None


class TestNothingTakesTheWorkerDown:
    def test_an_import_that_does_not_exist_is_noticed_rather_than_raised(
        self, scripted_worker
    ):
        scripted_worker().run_once("imp_nobody_made")  # must not raise

    def test_an_import_with_no_conversation_behind_it_fails_with_a_reason(
        self, ops_store, scripted_worker
    ):
        # The shape a crash partway through staging would leave behind: a
        # history row with no buffer under it. Nothing in the normal path
        # produces one, and the worker still has to say something useful.
        ops_store.imports.record(
            ImportRecord(
                import_id="imp_orphan",
                batch_id="batch_orphan",
                user_id="local",
                source_conversation_id="conv-orphan",
                event_date=AUG_2,
            )
        )

        scripted_worker().run_once("imp_orphan")

        record = ops_store.imports.get("imp_orphan")
        assert record.status is ImportStatus.FAILED
        assert "no stored conversation" in record.error

    def test_a_run_that_throws_is_recorded_as_a_failure(
        self, ops_store, scripted_worker, imported_conversation, monkeypatch
    ):
        staged = imported_conversation()
        worker = scripted_worker()

        def explode(*args, **kwargs):
            raise RuntimeError("the model went away")

        monkeypatch.setattr("lumen.ingest.worker.run_pipeline", explode)
        worker.run_once(staged.import_id)

        record = ops_store.imports.get(staged.import_id)
        assert record.status is ImportStatus.FAILED
        assert "the model went away" in record.error

    def test_the_kind_of_failure_is_named_not_just_the_message(
        self, ops_store, scripted_worker, imported_conversation, monkeypatch
    ):
        staged = imported_conversation()
        worker = scripted_worker()

        monkeypatch.setattr(
            "lumen.ingest.worker.run_pipeline",
            lambda *a, **k: (_ for _ in ()).throw(ProviderError("no credential")),
        )
        worker.run_once(staged.import_id)

        assert "ProviderError" in ops_store.imports.get(staged.import_id).error

    def test_a_failure_leaves_the_next_import_able_to_run(
        self, ops_store, scripted_worker, imported_conversation, monkeypatch
    ):
        first = imported_conversation()
        second = imported_conversation(source_conversation_id="conv-aug-3")
        worker = scripted_worker()

        monkeypatch.setattr(
            "lumen.ingest.worker.run_pipeline",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope")),
        )
        worker.run_once(first.import_id)
        monkeypatch.undo()
        worker.run_once(second.import_id)

        assert ops_store.imports.get(first.import_id).status is ImportStatus.FAILED
        assert ops_store.imports.get(second.import_id).status is ImportStatus.COMPLETE


class TestTheQueue:
    def test_a_submitted_import_is_worked_through(
        self, ops_store, scripted_worker, imported_conversation
    ):
        staged = imported_conversation()
        worker = scripted_worker()

        with worker:
            worker.submit(staged.import_id)
            _wait_until(
                lambda: ops_store.imports.get(staged.import_id).status
                is ImportStatus.COMPLETE
            )

        assert ops_store.imports.get(staged.import_id).status is ImportStatus.COMPLETE

    def test_several_imports_are_worked_through_one_at_a_time(
        self, ops_store, scripted_worker, imported_conversation
    ):
        staged = [
            imported_conversation(source_conversation_id=f"conv-{index}")
            for index in range(3)
        ]
        worker = scripted_worker()

        with worker:
            for item in staged:
                worker.submit(item.import_id)
            _wait_until(
                lambda: all(
                    ops_store.imports.get(item.import_id).status
                    is not ImportStatus.QUEUED
                    for item in staged
                )
            )

        assert [ops_store.imports.get(item.import_id).status for item in staged] == [
            ImportStatus.COMPLETE
        ] * 3

    def test_starting_a_worker_that_is_already_running_changes_nothing(
        self, scripted_worker
    ):
        worker = scripted_worker()
        try:
            worker.start()
            worker.start()
            assert (
                sum(1 for t in threading.enumerate() if t.name == "lumen-ingest") == 1
            )
        finally:
            worker.stop()

    def test_stopping_a_worker_that_never_started_is_harmless(self, scripted_worker):
        scripted_worker().stop()

    def test_what_is_waiting_can_be_counted(self, scripted_worker):
        worker = scripted_worker()
        worker.submit("imp_a")
        worker.submit("imp_b")

        assert worker.pending == 2

    def test_a_worker_that_will_not_stop_is_reported_rather_than_waited_on_forever(
        self, scripted_worker, caplog
    ):
        # An import that has wedged must not wedge the shutdown with it.
        # Saying so and moving on beats a service that will not exit.
        worker = scripted_worker()
        stuck = threading.Event()

        def never_finishes(_import_id: str) -> None:
            stuck.wait(timeout=10)

        worker.run_once = never_finishes
        worker.start()
        worker.submit("imp_wedged")
        try:
            with caplog.at_level("WARNING"):
                worker.stop(timeout=0.1)
            assert "did not stop in time" in caplog.text
        finally:
            stuck.set()


class TestExplainingAFailedRun:
    def test_the_episodes_own_reasons_are_reported(
        self, ops_store, scripted_worker, imported_conversation, monkeypatch
    ):
        # "FAILED" tells whoever uploaded the file nothing they can act on.
        from lumen.schemas.enums import EpisodeRunStatus, QualityGateDecision
        from lumen.schemas.pipeline import EpisodeOutcome, RunReport

        staged = imported_conversation()
        report = RunReport(
            job_id="job_x",
            session_id=staged.session_id,
            quality_gate_decision=QualityGateDecision.REFLECTION,
            job_status="FAILED",
            episodes=[
                EpisodeOutcome(
                    episode_id="ep_1",
                    status=EpisodeRunStatus.FAILED,
                    error="the reading came back unusable",
                )
            ],
        )
        monkeypatch.setattr("lumen.ingest.worker.run_pipeline", lambda *a, **k: report)

        scripted_worker().run_once(staged.import_id)

        record = ops_store.imports.get(staged.import_id)
        assert record.status is ImportStatus.FAILED
        assert record.error == "the reading came back unusable"

    def test_a_run_that_failed_with_no_episode_saying_why_still_says_something(
        self, ops_store, scripted_worker, imported_conversation, monkeypatch
    ):
        from lumen.schemas.enums import QualityGateDecision
        from lumen.schemas.pipeline import RunReport

        staged = imported_conversation()
        report = RunReport(
            job_id="job_y",
            session_id=staged.session_id,
            quality_gate_decision=QualityGateDecision.DISCARD,
            job_status="FAILED",
        )
        monkeypatch.setattr("lumen.ingest.worker.run_pipeline", lambda *a, **k: report)

        scripted_worker().run_once(staged.import_id)

        assert ops_store.imports.get(staged.import_id).error == "the run ended as FAILED"


class TestReadiness:
    def test_a_worker_given_its_resources_is_ready_without_building_any(
        self, scripted_worker
    ):
        assert scripted_worker().ensure_ready() is not None

    def test_a_worker_with_no_model_configured_says_so(
        self, ops_store, graph_store, monkeypatch
    ):
        # The point of asking before staging: "no model is configured" is a
        # refusal to accept the upload, not something discovered four
        # minutes into a run.
        monkeypatch.setenv("LUMEN_LIGHTWEIGHT_PROVIDER", "no_such_provider")
        worker = IngestWorker(
            config=AppConfig(), ops=ops_store, graph=graph_store, resources=None
        )

        with pytest.raises(ProviderError):
            worker.ensure_ready()

    def test_resources_are_only_built_once(self, ops_store, graph_store, monkeypatch):
        built: list[int] = []

        def count(config, graph):
            built.append(1)
            return IngestResources(
                graph=graph, vectors=None, embedder=None, lightweight=None, thinking=None
            )

        monkeypatch.setattr("lumen.ingest.worker.build_resources", count)
        worker = IngestWorker(config=AppConfig(), ops=ops_store, graph=graph_store)

        worker.ensure_ready()
        worker.ensure_ready()

        assert built == [1]


def _wait_until(condition, timeout: float = 30.0) -> None:
    """Wait for a background thread to get somewhere, or give up loudly."""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.02)
    raise AssertionError("the worker never got there")


class TestAConversationLumenHeldItself:
    """
    The other kind of work the queue carries.

    Identical to an import in everything that matters — the same thread, the
    same pipeline, the same serialisation — and different in the only place
    it can be: an import has a record somebody is watching, and a
    conversation has only itself.
    """

    def test_it_goes_through_the_same_queue(self, scripted_worker):
        worker = scripted_worker()

        worker.submit_session("sess_1")

        assert worker.pending == 1

    def test_an_empty_conversation_is_settled_rather_than_failing(
        self, ops_store, scripted_worker
    ):
        # A conversation with nothing in it is a real state — somebody opened
        # a chat and closed it — and the pipeline is what decides there was
        # nothing worth extracting.
        from lumen.operational.enums import BufferStatus
        from lumen.operational.schemas import SessionBufferRecord

        ops_store.buffers.create_buffer(
            SessionBufferRecord(
                session_id="sess_empty",
                user_id="local",
                event_date=AUG_2,
                session_label="empty",
            )
        )

        scripted_worker().run_session("sess_empty")

        assert ops_store.buffers.get_buffer("sess_empty").status in {
            BufferStatus.PROCESSED,
            BufferStatus.DISCARDED,
        }

    def test_a_failed_run_hands_the_conversation_back_rather_than_keeping_it(
        self, ops_store, scripted_worker, monkeypatch
    ):
        # Dispatched means somebody owns it. After a failure nobody does, and
        # leaving it there is how a conversation is never looked at again.
        from lumen.operational.enums import BufferStatus
        from lumen.operational.schemas import SessionBufferRecord

        ops_store.buffers.create_buffer(
            SessionBufferRecord(
                session_id="sess_broken",
                user_id="local",
                event_date=AUG_2,
                session_label="broken",
            )
        )
        ops_store.buffers.claim_for_processing("sess_broken", at=datetime.now(UTC))
        monkeypatch.setattr(
            "lumen.ingest.worker.run_pipeline",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("the graph is gone")),
        )

        scripted_worker().run_session("sess_broken")

        assert ops_store.buffers.get_buffer("sess_broken").status is BufferStatus.DECAYED

    def test_a_failure_does_not_take_the_worker_down(self, scripted_worker):
        worker = scripted_worker()

        worker.run_session("sess_that_does_not_exist")

    def test_what_happens_is_announced_when_anybody_asked_to_be_told(
        self, scripted_worker
    ):
        said: list[tuple[str, dict]] = []
        worker = scripted_worker()
        worker._announce = lambda kind, payload: said.append((kind, payload))

        worker.run_session("sess_that_does_not_exist")

        assert [kind for kind, _ in said] == ["run_started", "run_finished"]

    def test_a_listener_that_breaks_does_not_cost_the_run(self, scripted_worker):
        # Whoever is watching is a convenience; the entry being written is not.
        def explode(kind, payload):
            raise RuntimeError("the socket is gone")

        worker = scripted_worker()
        worker._announce = explode

        worker.run_session("sess_that_does_not_exist")
