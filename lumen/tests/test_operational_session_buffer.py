"""Tests for the store that holds conversations waiting to be processed."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from lumen.operational.enums import BufferSource, BufferStatus
from lumen.operational.repositories import RecordNotFoundError
from lumen.operational.schemas import BufferMessageRecord, SessionBufferRecord
from lumen.schemas.enums import DialogueAct, SourceModality

TODAY = date(2026, 6, 11)
NOW = datetime(2026, 6, 11, 21, 0, tzinfo=UTC)


def _message(seq: int, session_id: str, **overrides) -> BufferMessageRecord:
    # Message ids are unique across the whole store, not just within a buffer.
    defaults = {
        "message_id": f"{session_id}_msg_{seq}",
        "session_id": session_id,
        "seq": seq,
        "role": "USER",
        "content": f"message {seq}",
        "timestamp": NOW + timedelta(minutes=seq),
        "event_date": TODAY,
    }
    defaults.update(overrides)
    return BufferMessageRecord(**defaults)


class TestFindOrCreate:
    def test_creates_a_buffer_the_first_time(self, ops_store):
        buffer = ops_store.buffers.find_or_create("local", TODAY, "A")
        assert buffer.status == BufferStatus.OPEN
        assert buffer.message_count == 0
        assert buffer.session_label == "A"

    def test_returns_the_same_buffer_the_second_time(self, ops_store):
        """
        Two messages from one conversation must land in one buffer, otherwise
        a single conversation would be extracted as two.
        """
        first = ops_store.buffers.find_or_create("local", TODAY, "A")
        second = ops_store.buffers.find_or_create("local", TODAY, "A")
        assert first.session_id == second.session_id

    def test_different_labels_on_one_day_stay_separate(self, ops_store):
        """
        The user split these conversations by topic. Merging them would throw
        that intent away.
        """
        first = ops_store.buffers.find_or_create("local", TODAY, "A")
        second = ops_store.buffers.find_or_create("local", TODAY, "B")
        assert first.session_id != second.session_id

    def test_different_users_stay_separate(self, ops_store):
        first = ops_store.buffers.find_or_create("alice", TODAY, "A")
        second = ops_store.buffers.find_or_create("bob", TODAY, "A")
        assert first.session_id != second.session_id

    def test_the_generated_id_is_readable(self, ops_store):
        buffer = ops_store.buffers.find_or_create("local", TODAY, "A")
        assert buffer.session_id.startswith("sb_2026_06_11_a_")

    def test_an_unlabelled_buffer_gets_a_sensible_id(self, ops_store):
        buffer = ops_store.buffers.find_or_create("local", TODAY)
        assert "_main_" in buffer.session_id

    def test_the_source_is_remembered(self, ops_store):
        buffer = ops_store.buffers.find_or_create(
            "local", TODAY, "A", source=BufferSource.IMPORT_JSON
        )
        assert buffer.source == BufferSource.IMPORT_JSON


class TestCreateBuffer:
    def test_an_explicit_buffer_can_be_saved(self, ops_store):
        record = SessionBufferRecord(
            session_id="imported-session",
            user_id="local",
            event_date=TODAY,
            session_label="Imported",
            source=BufferSource.IMPORT_MARKDOWN,
        )
        assert ops_store.buffers.create_buffer(record) == "imported-session"
        assert ops_store.buffers.get_buffer("imported-session") is not None

    def test_an_unknown_buffer_reads_back_as_nothing(self, ops_store):
        assert ops_store.buffers.get_buffer("no-such-buffer") is None


class TestAppendMessage:
    def test_a_message_is_stored(self, ops_store):
        buffer = ops_store.buffers.find_or_create("local", TODAY, "A")
        ops_store.buffers.append_message(buffer.session_id, _message(0, buffer.session_id))
        assert len(ops_store.buffers.get_messages(buffer.session_id)) == 1

    def test_the_message_count_keeps_up(self, ops_store):
        buffer = ops_store.buffers.find_or_create("local", TODAY, "A")
        for seq in range(3):
            ops_store.buffers.append_message(
                buffer.session_id, _message(seq, buffer.session_id)
            )
        assert ops_store.buffers.get_buffer(buffer.session_id).message_count == 3

    def test_activity_time_moves_forward(self, ops_store):
        buffer = ops_store.buffers.find_or_create("local", TODAY, "A")
        before = ops_store.buffers.get_buffer(buffer.session_id).last_activity_at
        ops_store.buffers.append_message(buffer.session_id, _message(0, buffer.session_id))
        after = ops_store.buffers.get_buffer(buffer.session_id).last_activity_at
        assert after >= before

    def test_messages_come_back_in_order(self, ops_store):
        """
        Order comes from the sequence number, not the timestamp — imported
        conversations often carry unreliable times.
        """
        buffer = ops_store.buffers.find_or_create("local", TODAY, "A")
        for seq in (2, 0, 1):
            ops_store.buffers.append_message(
                buffer.session_id,
                _message(seq, buffer.session_id, timestamp=NOW),
            )
        assert [m.seq for m in ops_store.buffers.get_messages(buffer.session_id)] == [0, 1, 2]

    def test_optional_details_survive_the_round_trip(self, ops_store):
        buffer = ops_store.buffers.find_or_create("local", TODAY, "A")
        ops_store.buffers.append_message(
            buffer.session_id,
            _message(
                0,
                buffer.session_id,
                dialogue_act=DialogueAct.EXPRESSIVE,
                co_created_marker=True,
                role="AI",
            ),
        )
        stored = ops_store.buffers.get_messages(buffer.session_id)[0]
        assert stored.dialogue_act == DialogueAct.EXPRESSIVE
        assert stored.co_created_marker is True
        assert stored.role == "AI"

    def test_appending_to_a_missing_buffer_is_refused(self, ops_store):
        with pytest.raises(RecordNotFoundError, match="no session buffer"):
            ops_store.buffers.append_message("ghost", _message(0, "ghost"))


class TestFindDecayed:
    def test_a_quiet_buffer_is_found(self, ops_store, buffer_with_messages):
        cutoff = datetime.now(UTC) + timedelta(minutes=1)
        found = ops_store.buffers.find_decayed(cutoff)
        assert [b.session_id for b in found] == [buffer_with_messages.session_id]

    def test_a_recently_active_buffer_is_left_alone(self, ops_store, buffer_with_messages):
        cutoff = datetime.now(UTC) - timedelta(hours=1)
        assert ops_store.buffers.find_decayed(cutoff) == []

    def test_an_empty_buffer_is_never_processed(self, ops_store):
        """A conversation with no messages has nothing to extract."""
        ops_store.buffers.find_or_create("local", TODAY, "empty")
        cutoff = datetime.now(UTC) + timedelta(minutes=1)
        assert ops_store.buffers.find_decayed(cutoff) == []

    def test_buffers_already_being_processed_are_skipped(
        self, ops_store, buffer_with_messages
    ):
        ops_store.buffers.mark_status(
            buffer_with_messages.session_id, BufferStatus.DISPATCHED
        )
        cutoff = datetime.now(UTC) + timedelta(minutes=1)
        assert ops_store.buffers.find_decayed(cutoff) == []

    def test_the_quietest_buffer_comes_first(self, ops_store):
        for label in ("A", "B"):
            buffer = ops_store.buffers.find_or_create("local", TODAY, label)
            ops_store.buffers.append_message(
                buffer.session_id, _message(0, buffer.session_id)
            )

        cutoff = datetime.now(UTC) + timedelta(minutes=1)
        found = ops_store.buffers.find_decayed(cutoff)
        assert len(found) == 2
        assert found[0].last_activity_at <= found[1].last_activity_at

    def test_the_limit_is_respected(self, ops_store):
        for label in ("A", "B", "C"):
            buffer = ops_store.buffers.find_or_create("local", TODAY, label)
            ops_store.buffers.append_message(
                buffer.session_id, _message(0, buffer.session_id)
            )

        cutoff = datetime.now(UTC) + timedelta(minutes=1)
        assert len(ops_store.buffers.find_decayed(cutoff, limit=2)) == 2


class TestMarkStatus:
    def test_the_status_changes(self, ops_store, buffer_with_messages):
        updated = ops_store.buffers.mark_status(
            buffer_with_messages.session_id, BufferStatus.PROCESSED
        )
        assert updated.status == BufferStatus.PROCESSED

    def test_decaying_records_when_it_happened(self, ops_store, buffer_with_messages):
        updated = ops_store.buffers.mark_status(
            buffer_with_messages.session_id, BufferStatus.DECAYED
        )
        assert updated.decayed_at is not None

    def test_the_decay_time_is_not_overwritten(self, ops_store, buffer_with_messages):
        first = ops_store.buffers.mark_status(
            buffer_with_messages.session_id, BufferStatus.DECAYED
        )
        second = ops_store.buffers.mark_status(
            buffer_with_messages.session_id, BufferStatus.DECAYED
        )
        assert first.decayed_at == second.decayed_at

    def test_an_unknown_buffer_is_refused(self, ops_store):
        with pytest.raises(RecordNotFoundError):
            ops_store.buffers.mark_status("ghost", BufferStatus.DECAYED)


class TestBuildDecayEvent:
    def test_it_produces_what_the_pipeline_expects(self, ops_store, buffer_with_messages):
        event = ops_store.buffers.build_decay_event(buffer_with_messages.session_id)

        assert event.session_id == buffer_with_messages.session_id
        assert event.user_id == "local"
        assert event.event_date == TODAY
        assert event.message_count == 3
        assert len(event.raw_buffer) == 3

    def test_the_conversation_label_is_carried_through(
        self, ops_store, buffer_with_messages
    ):
        """
        Later stages record which conversation a result came from, so the
        label has to survive the handover.
        """
        event = ops_store.buffers.build_decay_event(buffer_with_messages.session_id)
        assert event.session_label == "A"

    def test_messages_stay_in_order(self, ops_store, buffer_with_messages):
        event = ops_store.buffers.build_decay_event(buffer_with_messages.session_id)
        assert [m.message_id for m in event.raw_buffer] == ["msg_0", "msg_1", "msg_2"]
        assert event.raw_buffer[1].role == "AI"

    def test_it_carries_the_current_trace_id(
        self, ops_store, buffer_with_messages, bound_trace
    ):
        event = ops_store.buffers.build_decay_event(buffer_with_messages.session_id)
        assert event.trace_id == bound_trace

    def test_the_decay_time_is_used_when_known(self, ops_store, buffer_with_messages):
        marked = ops_store.buffers.mark_status(
            buffer_with_messages.session_id, BufferStatus.DECAYED
        )
        event = ops_store.buffers.build_decay_event(buffer_with_messages.session_id)
        assert event.triggered_at == marked.decayed_at

    def test_an_unknown_buffer_is_refused(self, ops_store):
        with pytest.raises(RecordNotFoundError):
            ops_store.buffers.build_decay_event("ghost")

    def test_a_voice_buffer_is_handed_over_as_speech(self, ops_store):
        """
        Preprocessing skips its speech cleanup on typed input, so whether a
        session was spoken has to survive the handover. If it does not, an
        "um" someone actually said stays in their history forever.
        """
        buffer = ops_store.buffers.find_or_create(
            user_id="local",
            event_date=TODAY,
            session_label="spoken",
            source=BufferSource.VOICE_NOTE,
        )
        ops_store.buffers.append_message(buffer.session_id, _message(0, buffer.session_id))

        event = ops_store.buffers.build_decay_event(buffer.session_id)
        assert event.source_modality == SourceModality.VOICE_NOTE

    @pytest.mark.parametrize(
        "source",
        [BufferSource.NATIVE_CHAT, BufferSource.IMPORT_MARKDOWN, BufferSource.IMPORT_JSON],
    )
    def test_every_other_source_is_handed_over_as_typing(self, ops_store, source):
        buffer = ops_store.buffers.find_or_create(
            user_id="local", event_date=TODAY, session_label=source.value, source=source
        )
        ops_store.buffers.append_message(buffer.session_id, _message(0, buffer.session_id))

        event = ops_store.buffers.build_decay_event(buffer.session_id)
        assert event.source_modality == SourceModality.TEXT_ENTRY


class TestAskingForNoEarlierDays:
    def test_asking_for_none_reads_nothing_at_all(self, ops_store):
        """
        A ceiling of zero means no earlier days, not "all of them". Slicing
        would quietly turn one into the other.
        """
        from datetime import date

        assert (
            ops_store.buffers.recent_buffers("tester", before=date(2026, 8, 18), limit=0)
            == []
        )
