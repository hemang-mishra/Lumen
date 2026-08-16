"""
Tests for reading an exported conversation.

Nothing here stands anything up. The parser takes decoded JSON and returns
what it understood, so every assertion below is exact and the whole file
runs in milliseconds — which is the point of keeping the reading separate
from the writing.

The fixtures match the structure of a real export message for message, but
the writing in them is invented. A test file is a bad place to keep
somebody's journal.
"""

from __future__ import annotations

from datetime import date, timezone, timedelta

import pytest

from lumen.ingest.chatgpt_json import ExportFormatError, parse_export


def message(
    identifier: str,
    role: str,
    content: str,
    timestamp: str | None = "2026-08-02T10:16:15.611Z",
) -> dict:
    """One message in the shape an export writes it."""
    body: dict = {"id": identifier, "role": role, "content": content}
    if timestamp is not None:
        body["timestamp"] = timestamp
    return body


def conversation(*messages: dict, **overrides) -> dict:
    """One conversation in the shape an export writes it."""
    body = {
        "id": "6a6f18ef-3088-83e8-b4fe-caf926cc356d",
        "title": "Aug 2",
        "lastUpdated": "2026-08-02T14:10:59.170Z",
        "messages": list(messages),
    }
    body.update(overrides)
    return body


SIMPLE = conversation(
    message("m1", "user", "The morning got away from me again."),
    message("m2", "assistant", "What were you avoiding?", "2026-08-02T10:16:16.031Z"),
    message("m3", "user", "Starting, mostly.", "2026-08-02T10:26:54.153Z"),
)


class TestReadingAWholeFile:
    def test_a_single_conversation_object_is_read(self):
        plan = parse_export(SIMPLE, filename="aug2.json")

        assert plan.filename == "aug2.json"
        assert len(plan.conversations) == 1
        assert plan.rejected == []
        assert plan.message_count == 3

    def test_a_list_of_conversations_is_read(self):
        second = conversation(
            message("n1", "user", "A different day entirely.", "2026-08-03T09:00:00Z"),
            id="second-conversation",
            title="Aug 3",
        )

        plan = parse_export([SIMPLE, second])

        assert [c.title for c in plan.conversations] == ["Aug 2", "Aug 3"]

    def test_a_wrapper_object_holding_the_list_is_read(self):
        plan = parse_export({"conversations": [SIMPLE]})

        assert len(plan.conversations) == 1

    def test_something_that_is_not_an_export_at_all_is_refused(self):
        with pytest.raises(ExportFormatError, match="expected a conversation"):
            parse_export("just a string")

    def test_a_file_where_nothing_is_readable_is_refused(self):
        with pytest.raises(ExportFormatError, match="nothing in this file"):
            parse_export([conversation(id="empty")])


class TestTheMessages:
    def test_roles_are_mapped_onto_the_two_the_buffer_knows(self):
        conversation_read = parse_export(SIMPLE).conversations[0]

        assert [m.role for m in conversation_read.messages] == ["USER", "AI", "USER"]

    def test_alternative_role_names_are_understood(self):
        plan = parse_export(
            conversation(
                message("m1", "human", "mine"),
                message("m2", "ai", "not mine"),
            )
        )

        assert [m.role for m in plan.conversations[0].messages] == ["USER", "AI"]

    def test_roles_that_are_not_people_talking_are_dropped_and_counted(self):
        plan = parse_export(
            conversation(
                message("m0", "system", "You are a helpful assistant."),
                message("m1", "user", "The morning got away from me."),
                message("m2", "tool", '{"result": 3}'),
            )
        )

        conversation_read = plan.conversations[0]
        assert len(conversation_read.messages) == 1
        assert conversation_read.skipped_roles == {"system": 1, "tool": 1}

    def test_message_order_is_the_order_in_the_file(self):
        conversation_read = parse_export(SIMPLE).conversations[0]

        assert [m.message_id for m in conversation_read.messages] == ["m1", "m2", "m3"]

    def test_a_message_with_no_id_is_named_by_its_position(self):
        plan = parse_export(conversation({"role": "user", "content": "no id here"}))

        assert plan.conversations[0].messages[0].message_id == "msg-0"

    def test_blank_messages_are_dropped_and_counted(self):
        plan = parse_export(
            conversation(
                message("m1", "user", "   "),
                message("m2", "user", "something real"),
            )
        )

        conversation_read = plan.conversations[0]
        assert len(conversation_read.messages) == 1
        assert conversation_read.skipped_roles == {"empty": 1}

    def test_a_message_that_is_not_an_object_is_dropped(self):
        plan = parse_export(
            conversation("not a message", message("m1", "user", "real one"))
        )

        assert plan.conversations[0].skipped_roles == {"unreadable": 1}


class TestContentThatArrivesInPieces:
    def test_a_list_of_fragments_is_joined_back_together(self):
        plan = parse_export(
            conversation({"id": "m1", "role": "user", "content": ["first", "second"]})
        )

        assert plan.conversations[0].messages[0].content == "first\n\nsecond"

    def test_fragments_wrapped_in_objects_are_unwrapped(self):
        plan = parse_export(
            conversation(
                {
                    "id": "m1",
                    "role": "user",
                    "content": [{"text": "first"}, {"text": "second"}],
                }
            )
        )

        assert plan.conversations[0].messages[0].content == "first\n\nsecond"

    def test_a_parts_object_is_unwrapped(self):
        plan = parse_export(
            conversation(
                {"id": "m1", "role": "user", "content": {"parts": ["only one"]}}
            )
        )

        assert plan.conversations[0].messages[0].content == "only one"

    def test_content_of_an_unexpected_type_reads_as_empty(self):
        plan = parse_export(
            conversation(
                {"id": "m1", "role": "user", "content": 42},
                message("m2", "user", "real one"),
            )
        )

        assert plan.conversations[0].skipped_roles == {"empty": 1}


class TestExportArtefacts:
    def test_memory_citation_markers_are_removed_and_counted(self):
        plan = parse_export(
            conversation(
                message("m1", "user", "a real sentence"),
                message(
                    "m2",
                    "assistant",
                    "You have said this before. memcite\n\n\nAnd again. memcite",
                ),
            )
        )

        conversation_read = plan.conversations[0]
        assert "memcite" not in conversation_read.messages[1].content
        assert conversation_read.artefacts_removed == 2

    def test_removing_a_marker_does_not_leave_a_hole_in_the_paragraph(self):
        plan = parse_export(
            conversation(
                message("m1", "user", "real"),
                message("m2", "assistant", "One thought. memcite\n\n\n\nAnother."),
            )
        )

        assert plan.conversations[0].messages[1].content == "One thought.\n\nAnother."

    def test_a_message_that_is_only_a_marker_is_dropped(self):
        plan = parse_export(
            conversation(
                message("m1", "user", "real"),
                message("m2", "assistant", "memcite"),
            )
        )

        assert len(plan.conversations[0].messages) == 1


class TestTheLogicalDate:
    def test_the_whole_conversation_takes_the_date_of_its_first_message(self):
        conversation_read = parse_export(SIMPLE).conversations[0]

        assert conversation_read.event_date == date(2026, 8, 2)

    def test_a_conversation_running_past_midnight_stays_on_the_day_it_started(self):
        plan = parse_export(
            conversation(
                message("m1", "user", "late thinking", "2026-08-02T22:40:00Z"),
                message("m2", "assistant", "go on", "2026-08-03T00:15:00Z"),
                message("m3", "user", "still going", "2026-08-03T01:05:00Z"),
            )
        )

        assert plan.conversations[0].event_date == date(2026, 8, 2)

    def test_the_date_is_worked_out_in_the_zone_the_person_lives_in(self):
        # 21:00 UTC on the 2nd is already half past two in the morning on the
        # 3rd in India. Which day the entry belongs to is a question about
        # the person's calendar, not the exporter's.
        india = timezone(timedelta(hours=5, minutes=30))
        payload = conversation(
            message("m1", "user", "late one", "2026-08-02T21:00:00Z")
        )

        assert parse_export(payload).conversations[0].event_date == date(2026, 8, 2)
        assert (
            parse_export(payload, local_timezone=india).conversations[0].event_date
            == date(2026, 8, 3)
        )


class TestTimestamps:
    def test_a_trailing_z_is_read_as_utc(self):
        conversation_read = parse_export(SIMPLE).conversations[0]
        first = conversation_read.messages[0].timestamp

        assert first.tzinfo is not None
        assert first.hour == 10

    def test_an_offset_is_kept(self):
        plan = parse_export(
            conversation(message("m1", "user", "hello", "2026-08-02T10:00:00+05:30"))
        )

        assert plan.conversations[0].messages[0].timestamp.utcoffset() == timedelta(
            hours=5, minutes=30
        )

    def test_a_bare_time_with_no_zone_is_read_as_utc(self):
        plan = parse_export(
            conversation(message("m1", "user", "hello", "2026-08-02T10:00:00"))
        )

        assert plan.conversations[0].messages[0].timestamp.utcoffset() == timedelta(0)

    def test_seconds_since_the_epoch_are_understood(self):
        plan = parse_export(
            conversation({"id": "m1", "role": "user", "content": "hi", "create_time": 1785665775.6})
        )

        assert plan.conversations[0].messages[0].timestamp.year == 2026

    def test_a_message_with_no_time_inherits_the_one_before_it(self):
        plan = parse_export(
            conversation(
                message("m1", "user", "first", "2026-08-02T10:00:00Z"),
                message("m2", "assistant", "second", None),
            )
        )

        stamps = [m.timestamp for m in plan.conversations[0].messages]
        assert stamps[0] == stamps[1]

    def test_the_first_message_falls_back_to_the_conversations_own_time(self):
        plan = parse_export(
            conversation(message("m1", "user", "undated", None))
        )

        assert plan.conversations[0].event_date == date(2026, 8, 2)

    def test_an_unparseable_time_is_treated_as_no_time_at_all(self):
        plan = parse_export(
            conversation(
                message("m1", "user", "first", "the second of August"),
            )
        )

        # Falls back to lastUpdated rather than being thrown away.
        assert plan.conversations[0].event_date == date(2026, 8, 2)

    def test_a_message_with_no_time_anywhere_to_fall_back_on_is_dropped(self):
        plan = parse_export(
            [
                conversation(
                    message("m1", "user", "undated", None),
                    message("m2", "user", "dated", "2026-08-05T09:00:00Z"),
                    lastUpdated=None,
                )
            ]
        )

        conversation_read = plan.conversations[0]
        assert conversation_read.skipped_roles == {"undated": 1}
        assert conversation_read.event_date == date(2026, 8, 5)


class TestRejections:
    def test_a_conversation_with_no_messages_is_rejected_with_a_reason(self):
        plan = parse_export([SIMPLE, conversation(id="hollow", title="Empty")])

        assert len(plan.conversations) == 1
        assert plan.rejected[0].source_conversation_id == "hollow"
        assert plan.rejected[0].reason == "it has no messages"

    def test_a_conversation_of_only_assistant_replies_is_rejected(self):
        plan = parse_export(
            [
                SIMPLE,
                conversation(
                    message("x1", "assistant", "a monologue"), id="one-sided"
                ),
            ]
        )

        assert plan.rejected[0].reason == "it contains nothing the person wrote themselves"

    def test_a_conversation_whose_messages_are_all_unreadable_is_rejected(self):
        plan = parse_export(
            [SIMPLE, conversation(message("x1", "system", "setup"), id="system-only")]
        )

        assert plan.rejected[0].reason == "none of its messages could be read"

    def test_an_entry_that_is_not_an_object_is_rejected_by_position(self):
        plan = parse_export([SIMPLE, "not a conversation"])

        assert plan.rejected[0].reason == "entry 1 is not a conversation object"

    def test_readable_conversations_survive_their_unreadable_neighbours(self):
        plan = parse_export([conversation(id="hollow"), SIMPLE, "rubbish"])

        assert len(plan.conversations) == 1
        assert len(plan.rejected) == 2


class TestIdentity:
    def test_the_exports_own_id_is_the_dedupe_key(self):
        conversation_read = parse_export(SIMPLE).conversations[0]

        assert (
            conversation_read.source_conversation_id
            == "6a6f18ef-3088-83e8-b4fe-caf926cc356d"
        )

    def test_a_conversation_with_no_id_gets_a_stable_one(self):
        payload = conversation(message("m1", "user", "hello"), id=None)

        first = parse_export(payload).conversations[0].source_conversation_id
        second = parse_export(payload).conversations[0].source_conversation_id

        assert first == second
        assert first.startswith("conv-0-")

    def test_two_unnamed_conversations_do_not_collide(self):
        plan = parse_export(
            [
                conversation(message("m1", "user", "one"), id=None),
                conversation(message("m2", "user", "two"), id=None),
            ]
        )

        ids = {c.source_conversation_id for c in plan.conversations}
        assert len(ids) == 2

    def test_a_missing_title_is_empty_rather_than_absent(self):
        plan = parse_export(conversation(message("m1", "user", "hello"), title=None))

        assert plan.conversations[0].title == ""


class TestCounts:
    def test_a_conversation_knows_how_much_of_it_the_person_wrote(self):
        assert parse_export(SIMPLE).conversations[0].user_message_count == 2

    def test_the_plan_counts_every_message_it_kept(self):
        plan = parse_export([SIMPLE, SIMPLE])

        assert plan.message_count == 6
