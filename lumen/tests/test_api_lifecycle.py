"""
The whole product, once, in order.

Everything else in this suite tests one part. This tests the join: somebody
talks to Lumen, walks away, and later asks something that only makes sense if
what they said became history. Every step of that is a different goal's work,
and until the clock existed the chain had a gap in the middle that no test
could see, because each half passed on its own.

The gap was this: a conversation Lumen held itself never reached the pipeline.
The pipeline worked. The conversation worked. Nothing joined them.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from lumen.config import AppConfig, OperationalConfig, SchedulerConfig
from lumen.ingest.worker import IngestResources, IngestWorker
from lumen.operational.enums import BufferSource, BufferStatus
from lumen.operational.schemas import BufferMessageRecord, SessionBufferRecord
from lumen.scheduling import Scheduler
from lumen.scheduling.watcher import DecayedConversationWatcher
from lumen.simulation import CORPUS
from lumen.simulation.runner import build_embedder, build_models

NOW = datetime(2026, 8, 20, 21, tzinfo=UTC)
USER = "local"


@pytest.fixture
def a_whole_evening(graph_store, vector_store, ops_store, ops_config, tmp_path):
    """
    Everything the gateway wires together, pointed at temporary stores.

    Built the way the application builds it — a worker holding the models, a
    watcher over the store, and one clock driving them — so what this
    exercises is the shipped path rather than a rehearsal of it.
    """
    day = CORPUS[0]
    script, lightweight, thinking = build_models([day])
    script.begin(day)

    worker = IngestWorker(
        config=AppConfig(operational=ops_config, user_id=USER),
        ops=ops_store,
        graph=graph_store,
        resources=IngestResources(
            graph=graph_store,
            vectors=vector_store,
            embedder=build_embedder(768),
            lightweight=lightweight,
            thinking=thinking,
        ),
    )
    watcher = DecayedConversationWatcher(
        ops=ops_store,
        worker=worker,
        config=AppConfig(
            operational=OperationalConfig(
                db_url=ops_config.db_url, session_decay_minutes=120
            ),
            scheduler=SchedulerConfig(enabled=False),
            user_id=USER,
        ),
    )
    return {
        "day": day,
        "worker": worker,
        "watcher": watcher,
        "graph": graph_store,
        "ops": ops_store,
    }


def talk(ops, day, *, quiet_for_minutes: int, engine) -> str:
    """
    Say a day's worth of things to Lumen and then walk away.

    Written through the conversation's own store, exactly as a chat turn
    arrives, because the point of the test is that a conversation held here
    is enough.
    """
    session_id = f"chat_{day.event_date:%Y_%m_%d}"
    ops.buffers.create_buffer(
        SessionBufferRecord(
            session_id=session_id,
            user_id=USER,
            event_date=day.event_date,
            session_label="evening",
            source=BufferSource.NATIVE_CHAT,
        )
    )
    ops.buffers.append_message(
        session_id,
        BufferMessageRecord(
            message_id=f"msg_{session_id}",
            session_id=session_id,
            seq=0,
            role="USER",
            content=day.text,
            timestamp=NOW - timedelta(minutes=quiet_for_minutes),
            event_date=day.event_date,
        ),
    )
    _went_quiet(engine, session_id, NOW - timedelta(minutes=quiet_for_minutes))
    return session_id


def _went_quiet(engine, session_id: str, at: datetime) -> None:
    """Say when a conversation was last spoken in."""
    from sqlalchemy import update
    from sqlalchemy.orm import Session

    from lumen.operational import models

    with Session(engine) as db:
        db.execute(
            update(models.SessionBuffer)
            .where(models.SessionBuffer.session_id == session_id)
            .values(last_activity_at=at)
        )
        db.commit()


class TestTalkingToLumenIsEnough:
    def test_a_finished_conversation_becomes_history_with_nobody_pressing_anything(
        self, a_whole_evening, ops_engine
    ):
        # The named test for this goal, and the gap it closes. Before the
        # clock existed, the only way to get here was to export the
        # conversation and upload it back to yourself.
        ops = a_whole_evening["ops"]
        graph = a_whole_evening["graph"]
        session_id = talk(
            ops, a_whole_evening["day"], quiet_for_minutes=200, engine=ops_engine
        )
        before = graph.count_by_type()

        dispatched = a_whole_evening["watcher"].run(NOW)
        a_whole_evening["worker"].run_session(session_id)

        after = graph.count_by_type()
        assert dispatched == 1
        assert after.get("EpisodeNode", 0) > before.get("EpisodeNode", 0)
        assert after.get("ObservationNode", 0) > before.get("ObservationNode", 0)

    def test_the_conversation_is_marked_as_dealt_with(
        self, a_whole_evening, ops_engine
    ):
        ops = a_whole_evening["ops"]
        session_id = talk(
            ops, a_whole_evening["day"], quiet_for_minutes=200, engine=ops_engine
        )

        a_whole_evening["watcher"].run(NOW)
        a_whole_evening["worker"].run_session(session_id)

        assert ops.buffers.get_buffer(session_id).status in {
            BufferStatus.PROCESSED,
            BufferStatus.DISCARDED,
        }

    def test_what_was_said_can_be_found_again_afterwards(
        self, a_whole_evening, ops_engine
    ):
        # The whole point. A record that was written and cannot be retrieved
        # is a record that did not help anybody.
        ops = a_whole_evening["ops"]
        graph = a_whole_evening["graph"]
        session_id = talk(
            ops, a_whole_evening["day"], quiet_for_minutes=200, engine=ops_engine
        )

        a_whole_evening["watcher"].run(NOW)
        a_whole_evening["worker"].run_session(session_id)

        episodes = graph.find_nodes(["EpisodeNode"], active_only=False, limit=10)
        assert episodes
        assert any(row.get("entry_id") == session_id for row in episodes)

    def test_a_conversation_still_being_talked_in_is_left_where_it_is(
        self, a_whole_evening, ops_engine
    ):
        ops = a_whole_evening["ops"]
        session_id = talk(
            ops, a_whole_evening["day"], quiet_for_minutes=5, engine=ops_engine
        )

        assert a_whole_evening["watcher"].run(NOW) == 0
        assert ops.buffers.get_buffer(session_id).status is BufferStatus.OPEN


class TestTheClockDrivingIt:
    def test_one_pass_of_the_clock_does_the_noticing(
        self, a_whole_evening, ops_engine
    ):
        # The scheduler decides when; the watcher decides what. This checks
        # they are actually joined up.
        ops = a_whole_evening["ops"]
        talk(ops, a_whole_evening["day"], quiet_for_minutes=200, engine=ops_engine)
        clock = Scheduler(
            [a_whole_evening["watcher"]], config=SchedulerConfig(enabled=False)
        )

        report = clock.tick(NOW)

        assert report.outcomes[0].name == "session-decay"
        assert report.outcomes[0].did == 1

    def test_a_second_pass_finds_nothing_left_to_do(
        self, a_whole_evening, ops_engine
    ):
        ops = a_whole_evening["ops"]
        talk(ops, a_whole_evening["day"], quiet_for_minutes=200, engine=ops_engine)
        clock = Scheduler(
            [a_whole_evening["watcher"]],
            config=SchedulerConfig(enabled=False, watch_every_seconds=0),
        )

        clock.tick(NOW)
        second = clock.tick(NOW + timedelta(minutes=1))

        assert second.outcomes[0].did == 0
