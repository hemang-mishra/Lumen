"""
Tests for writing an understood file into the waiting room.

These run against a real operational database rather than a stand-in,
because everything worth checking here is about what the database ends up
holding — that a conversation lands whole, that it lands only once, and that
it never lands on top of somebody else's.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from lumen.ingest.contracts import ImportPlan, ParsedConversation, ParsedMessage
from lumen.ingest.loader import stage_conversations
from lumen.operational.enums import BufferSource, BufferStatus, ImportStatus
from lumen.operational.schemas import BufferMessageRecord

AUG_2 = date(2026, 8, 2)


def a_message(index: int, role: str = "USER", content: str = "something real") -> ParsedMessage:
    """One parsed message, already mapped onto Lumen's vocabulary."""
    return ParsedMessage(
        message_id=f"m{index}",
        role=role,
        content=content,
        timestamp=datetime(2026, 8, 2, 10, index, tzinfo=UTC),
    )


def a_conversation(**overrides) -> ParsedConversation:
    """One parsed conversation, ready to stage."""
    fields = {
        "source_conversation_id": "conv-aug-2",
        "title": "Aug 2",
        "event_date": AUG_2,
        "messages": [a_message(0), a_message(1, "AI", "what were you avoiding?")],
    }
    fields.update(overrides)
    return ParsedConversation(**fields)


def a_plan(*conversations: ParsedConversation, filename: str = "aug2.json") -> ImportPlan:
    """One uploaded file's worth of readable conversations."""
    return ImportPlan(
        filename=filename,
        conversations=list(conversations) or [a_conversation()],
    )


class TestStagingOneConversation:
    def test_its_messages_end_up_in_a_buffer(self, ops_store):
        staged = stage_conversations(a_plan(), ops=ops_store, user_id="local")

        messages = ops_store.buffers.get_messages(staged[0].session_id)
        assert [message.content for message in messages] == [
            "something real",
            "what were you avoiding?",
        ]

    def test_the_order_of_the_conversation_survives(self, ops_store):
        staged = stage_conversations(
            a_plan(
                a_conversation(
                    messages=[a_message(i, content=f"line {i}") for i in range(5)]
                )
            ),
            ops=ops_store,
            user_id="local",
        )

        messages = ops_store.buffers.get_messages(staged[0].session_id)
        assert [message.seq for message in messages] == [0, 1, 2, 3, 4]
        assert [message.content for message in messages] == [f"line {i}" for i in range(5)]

    def test_the_buffer_says_it_came_from_a_file(self, ops_store):
        staged = stage_conversations(a_plan(), ops=ops_store, user_id="local")

        buffer = ops_store.buffers.get_buffer(staged[0].session_id)
        assert buffer.source is BufferSource.IMPORT_JSON

    def test_an_imported_conversation_is_finished_the_moment_it_arrives(self, ops_store):
        # Nobody is going to add to a file that has already been exported,
        # so it goes straight to the state a live conversation reaches only
        # after sitting quiet for two hours.
        staged = stage_conversations(a_plan(), ops=ops_store, user_id="local")

        assert ops_store.buffers.get_buffer(staged[0].session_id).status is BufferStatus.DECAYED

    def test_the_conversations_title_becomes_the_buffers_label(self, ops_store):
        staged = stage_conversations(a_plan(), ops=ops_store, user_id="local")

        assert ops_store.buffers.get_buffer(staged[0].session_id).session_label == "Aug 2"

    def test_a_conversation_with_no_title_still_gets_a_label(self, ops_store):
        staged = stage_conversations(
            a_plan(a_conversation(title="")), ops=ops_store, user_id="local"
        )

        assert ops_store.buffers.get_buffer(staged[0].session_id).session_label == "imported"

    def test_a_very_long_title_is_cut_rather_than_refused(self, ops_store):
        staged = stage_conversations(
            a_plan(a_conversation(title="x" * 300)), ops=ops_store, user_id="local"
        )

        assert len(ops_store.buffers.get_buffer(staged[0].session_id).session_label) == 64


class TestTheLogicalDate:
    def test_every_message_is_filed_under_the_conversations_own_day(self, ops_store):
        # The timestamps run past midnight; the day does not.
        after_midnight = ParsedMessage(
            message_id="late",
            role="USER",
            content="still going",
            timestamp=datetime(2026, 8, 3, 1, 5, tzinfo=UTC),
        )
        staged = stage_conversations(
            a_plan(a_conversation(messages=[a_message(0), after_midnight])),
            ops=ops_store,
            user_id="local",
        )

        messages = ops_store.buffers.get_messages(staged[0].session_id)
        assert {message.event_date for message in messages} == {AUG_2}

    def test_the_real_time_each_message_was_sent_is_kept(self, ops_store):
        # The day is one thing; the clock is another, and the second is what
        # tells the pipeline the order an evening actually happened in.
        staged = stage_conversations(a_plan(), ops=ops_store, user_id="local")

        stamps = [m.timestamp for m in ops_store.buffers.get_messages(staged[0].session_id)]
        assert stamps[0] < stamps[1]

    def test_the_buffer_is_filed_under_the_same_day(self, ops_store):
        staged = stage_conversations(a_plan(), ops=ops_store, user_id="local")

        assert ops_store.buffers.get_buffer(staged[0].session_id).event_date == AUG_2


class TestTheHistoryRow:
    def test_an_import_is_recorded_for_every_conversation(self, ops_store):
        staged = stage_conversations(a_plan(), ops=ops_store, user_id="local")

        record = ops_store.imports.get(staged[0].import_id)
        assert record.title == "Aug 2"
        assert record.filename == "aug2.json"
        assert record.message_count == 2
        assert record.status is ImportStatus.QUEUED
        assert record.session_id == staged[0].session_id

    def test_every_conversation_in_one_file_shares_a_batch(self, ops_store):
        stage_conversations(
            a_plan(
                a_conversation(),
                a_conversation(
                    source_conversation_id="conv-aug-3",
                    title="Aug 3",
                    event_date=date(2026, 8, 3),
                ),
            ),
            ops=ops_store,
            user_id="local",
            batch_id="batch_xyz",
        )

        assert len(ops_store.imports.get_batch("batch_xyz").imports) == 2

    def test_a_batch_is_invented_when_the_caller_does_not_supply_one(self, ops_store):
        staged = stage_conversations(a_plan(), ops=ops_store, user_id="local")

        assert ops_store.imports.get(staged[0].import_id).batch_id.startswith("batch_")


class TestUploadingTheSameFileTwice:
    def test_the_second_upload_stages_nothing(self, ops_store):
        stage_conversations(a_plan(), ops=ops_store, user_id="local")

        again = stage_conversations(a_plan(), ops=ops_store, user_id="local")

        assert again[0].already_imported is True
        assert len(ops_store.imports.list_recent("local")) == 1

    def test_the_second_upload_points_at_the_original_run(self, ops_store):
        first = stage_conversations(a_plan(), ops=ops_store, user_id="local")

        again = stage_conversations(a_plan(), ops=ops_store, user_id="local")

        assert again[0].import_id == first[0].import_id
        assert again[0].session_id == first[0].session_id

    def test_the_messages_are_not_written_a_second_time(self, ops_store):
        first = stage_conversations(a_plan(), ops=ops_store, user_id="local")
        stage_conversations(a_plan(), ops=ops_store, user_id="local")

        assert len(ops_store.buffers.get_messages(first[0].session_id)) == 2

    def test_a_new_conversation_alongside_a_repeat_is_still_staged(self, ops_store):
        stage_conversations(a_plan(), ops=ops_store, user_id="local")

        mixed = stage_conversations(
            a_plan(
                a_conversation(),
                a_conversation(source_conversation_id="conv-aug-3", title="Aug 3"),
            ),
            ops=ops_store,
            user_id="local",
        )

        assert [item.already_imported for item in mixed] == [True, False]

    def test_another_person_importing_the_same_export_is_not_a_repeat(self, ops_store):
        stage_conversations(a_plan(), ops=ops_store, user_id="local")

        theirs = stage_conversations(a_plan(), ops=ops_store, user_id="someone_else")

        assert theirs[0].already_imported is False


class TestNotLandingOnSomebodyElsesBuffer:
    def test_two_conversations_sharing_a_day_and_a_title_get_separate_buffers(
        self, ops_store
    ):
        # Merging them would read two separate pieces of thinking as one
        # entry. Worse, the first is already staged and finished, so the
        # merged buffer would be processed twice with different contents.
        staged = stage_conversations(
            a_plan(
                a_conversation(source_conversation_id="conv-one"),
                a_conversation(source_conversation_id="conv-two"),
            ),
            ops=ops_store,
            user_id="local",
        )

        assert staged[0].session_id != staged[1].session_id

    def test_the_second_buffer_is_named_after_the_conversation_itself(self, ops_store):
        staged = stage_conversations(
            a_plan(
                a_conversation(source_conversation_id="conv-one"),
                a_conversation(source_conversation_id="conv-aabbccdd"),
            ),
            ops=ops_store,
            user_id="local",
        )

        label = ops_store.buffers.get_buffer(staged[1].session_id).session_label
        assert label.endswith("aabbccdd")

    def test_an_import_never_joins_a_conversation_the_person_is_still_having(
        self, ops_store
    ):
        live = ops_store.buffers.find_or_create("local", AUG_2, session_label="Aug 2")
        ops_store.buffers.append_message(
            live.session_id,
            BufferMessageRecord(
                message_id="live_0",
                session_id=live.session_id,
                seq=0,
                role="USER",
                content="typed just now",
                timestamp=datetime(2026, 8, 2, 9, 0, tzinfo=UTC),
                event_date=AUG_2,
            ),
        )

        staged = stage_conversations(a_plan(), ops=ops_store, user_id="local")

        assert staged[0].session_id != live.session_id
        assert len(ops_store.buffers.get_messages(live.session_id)) == 1

    def test_message_ids_from_two_exports_do_not_collide(self, ops_store):
        # Message id is the primary key across every buffer there has ever
        # been, and two exports have no reason to have avoided each other's.
        staged = stage_conversations(
            a_plan(
                a_conversation(source_conversation_id="conv-one"),
                a_conversation(source_conversation_id="conv-two"),
            ),
            ops=ops_store,
            user_id="local",
        )

        first = {m.message_id for m in ops_store.buffers.get_messages(staged[0].session_id)}
        second = {m.message_id for m in ops_store.buffers.get_messages(staged[1].session_id)}
        assert first.isdisjoint(second)


class TestWhatTheCallerIsHandedBack:
    def test_one_entry_per_conversation_in_the_order_of_the_file(self, ops_store):
        staged = stage_conversations(
            a_plan(
                a_conversation(title="first", source_conversation_id="c1"),
                a_conversation(title="second", source_conversation_id="c2"),
            ),
            ops=ops_store,
            user_id="local",
        )

        assert [item.title for item in staged] == ["first", "second"]

    def test_a_file_with_nothing_readable_stages_nothing(self, ops_store):
        assert stage_conversations(
            ImportPlan(filename="empty.json"), ops=ops_store, user_id="local"
        ) == []
