"""
Tests for noticing that a conversation has finished.

This is the join that was never made — talking to Lumen stored every turn and
nothing ever handed the result to the pipeline. Most of the file is about the
two ways that could go wrong once it exists: taking a conversation somebody
else owns, and taking one twice.

The claim test is the one that matters. Everything else here would still
mostly work if it failed; that one is the difference between one evening
becoming one history and one evening becoming two.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from lumen.config import AppConfig, OperationalConfig, SchedulerConfig
from lumen.operational.enums import BufferSource, BufferStatus
from lumen.operational.schemas import BufferMessageRecord, SessionBufferRecord
from lumen.scheduling.watcher import DecayedConversationWatcher

NOW = datetime(2026, 8, 20, 18, tzinfo=UTC)


class Spy:
    """A worker that records what it was handed and runs nothing."""

    def __init__(self) -> None:
        self.taken: list[str] = []

    def submit_session(self, session_id: str) -> None:
        self.taken.append(session_id)


@pytest.fixture
def conversation(ops_store, ops_engine):
    """
    Put one conversation in the store, with a chosen age and origin.

    The age is written directly, because appending a message stamps the
    conversation with the real clock and every rule being tested here is
    about how long ago somebody last said something.
    """

    def _write(
        session_id: str,
        *,
        quiet_for_minutes: int = 200,
        source: BufferSource = BufferSource.NATIVE_CHAT,
        label: str | None = None,
    ) -> str:
        ops_store.buffers.create_buffer(
            SessionBufferRecord(
                session_id=session_id,
                user_id="tester",
                event_date=date(2026, 8, 20),
                session_label=label or session_id,
                source=source,
            )
        )
        ops_store.buffers.append_message(
            session_id,
            BufferMessageRecord(
                message_id=f"msg_{session_id}",
                session_id=session_id,
                seq=1,
                role="USER",
                content="something that happened today",
                timestamp=NOW - timedelta(minutes=quiet_for_minutes),
                event_date=date(2026, 8, 20),
            ),
        )
        _went_quiet_at(ops_engine, session_id, NOW - timedelta(minutes=quiet_for_minutes))
        return session_id

    return _write


def _went_quiet_at(engine, session_id: str, at: datetime) -> None:
    """Say when this conversation was last spoken in."""
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


@pytest.fixture
def watcher(ops_store, ops_config):
    """The watcher over the real store, with a chosen worker."""

    def _build(worker, **scheduler) -> DecayedConversationWatcher:
        return DecayedConversationWatcher(
            ops=ops_store,
            worker=worker,
            config=AppConfig(
                operational=OperationalConfig(
                    db_url=ops_config.db_url, session_decay_minutes=120
                ),
                scheduler=SchedulerConfig(**scheduler),
            ),
        )

    return _build


class TestWhatGetsPickedUp:
    def test_a_conversation_that_has_gone_quiet_is_handed_over(
        self, watcher, conversation
    ):
        conversation("sess_quiet", quiet_for_minutes=200)
        worker = Spy()

        assert watcher(worker).run(NOW) == 1
        assert worker.taken == ["sess_quiet"]

    def test_one_still_being_talked_in_is_left_alone(self, watcher, conversation):
        conversation("sess_live", quiet_for_minutes=5)
        worker = Spy()

        assert watcher(worker).run(NOW) == 0
        assert worker.taken == []

    def test_the_cutoff_is_where_it_says_it_is(self, watcher, conversation):
        conversation("sess_edge", quiet_for_minutes=119)
        worker = Spy()

        assert watcher(worker).run(NOW) == 0
        assert watcher(worker).run(NOW + timedelta(minutes=2)) == 1

    def test_an_empty_store_costs_nothing(self, watcher):
        assert watcher(Spy()).run(NOW) == 0

    def test_a_voice_note_is_ours_too(self, watcher, conversation):
        conversation("sess_spoken", source=BufferSource.VOICE_NOTE)

        assert watcher(Spy()).run(NOW) == 1


class TestWhatBelongsToSomebodyElse:
    @pytest.mark.parametrize(
        "source", [BufferSource.IMPORT_JSON, BufferSource.IMPORT_MARKDOWN]
    )
    def test_an_imported_conversation_is_left_to_its_owner(
        self, watcher, conversation, source
    ):
        # It sits in this table in this state while the importer works
        # through it. Taking it would mean one evening becoming two sets of
        # history.
        conversation("sess_imported", source=source)
        worker = Spy()

        assert watcher(worker).run(NOW) == 0
        assert worker.taken == []

    def test_an_imported_one_is_not_even_claimed(
        self, watcher, conversation, ops_store
    ):
        conversation("sess_imported", source=BufferSource.IMPORT_JSON)

        watcher(Spy()).run(NOW)

        assert ops_store.buffers.get_buffer("sess_imported").status is BufferStatus.OPEN


class TestTakingItOnce:
    def test_a_second_look_does_not_hand_it_over_again(
        self, watcher, conversation
    ):
        # The claim is what makes this true. Without it the second pass would
        # find the same conversation still open and dispatch it again.
        conversation("sess_quiet")
        worker = Spy()
        watching = watcher(worker)

        watching.run(NOW)
        watching.run(NOW + timedelta(minutes=1))

        assert worker.taken == ["sess_quiet"]

    def test_two_watchers_racing_dispatch_it_once_between_them(
        self, watcher, conversation
    ):
        # The importer is the real second claimant. Two watchers is the same
        # race, expressed in one process.
        conversation("sess_quiet")
        first, second = Spy(), Spy()

        watcher(first).run(NOW)
        watcher(second).run(NOW)

        assert first.taken + second.taken == ["sess_quiet"]

    def test_claiming_says_whether_it_won(self, ops_store, conversation):
        conversation("sess_quiet")

        assert ops_store.buffers.claim_for_processing("sess_quiet", at=NOW) is True
        assert ops_store.buffers.claim_for_processing("sess_quiet", at=NOW) is False

    def test_claiming_something_that_is_not_there_is_not_an_error(self, ops_store):
        assert ops_store.buffers.claim_for_processing("sess_never", at=NOW) is False

    def test_a_claimed_conversation_says_it_is_dispatched(
        self, watcher, conversation, ops_store
    ):
        conversation("sess_quiet")

        watcher(Spy()).run(NOW)

        buffer = ops_store.buffers.get_buffer("sess_quiet")
        assert buffer.status is BufferStatus.DISPATCHED
        assert buffer.decayed_at is not None


class TestLosingTheRace:
    def test_a_conversation_taken_between_looking_and_claiming_is_left_alone(
        self, ops_store, conversation
    ):
        # The gap the claim exists to close. Two things read the same list,
        # and one of them claims it first.
        conversation("sess_quiet")
        worker = Spy()

        class TakenFirst:
            """A store where somebody always gets there before we do."""

            def __getattr__(self, name):
                return getattr(ops_store, name)

            @property
            def buffers(self):
                return _Buffers(ops_store.buffers)

        class _Buffers:
            def __init__(self, real):
                self._real = real

            def __getattr__(self, name):
                return getattr(self._real, name)

            def claim_for_processing(self, session_id, *, at):
                return False

        watching = DecayedConversationWatcher(
            ops=TakenFirst(),
            worker=worker,
            config=AppConfig(
                operational=OperationalConfig(session_decay_minutes=120),
                scheduler=SchedulerConfig(),
            ),
        )

        assert watching.run(NOW) == 0
        assert worker.taken == []


class TestHowManyAtOnce:
    def test_a_backlog_is_handed_over_a_few_at_a_time(self, watcher, conversation):
        # Somebody importing a year of history should not have every day of
        # it dispatched in the same minute.
        for index in range(8):
            conversation(f"sess_{index}", label=f"label_{index}")
        worker = Spy()

        assert watcher(worker, max_dispatch_per_tick=3).run(NOW) == 3

    def test_the_rest_are_taken_on_the_next_look(self, watcher, conversation):
        for index in range(5):
            conversation(f"sess_{index}", label=f"label_{index}")
        worker = Spy()
        watching = watcher(worker, max_dispatch_per_tick=2)

        watching.run(NOW)
        watching.run(NOW + timedelta(minutes=1))

        assert len(worker.taken) == 4
        assert len(set(worker.taken)) == 4


class TestHowOftenItLooks:
    def test_it_takes_its_interval_from_the_settings(self, watcher):
        watching = watcher(Spy(), watch_every_seconds=90)

        assert watching.every == timedelta(seconds=90)
