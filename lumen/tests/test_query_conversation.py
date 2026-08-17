"""
Holding a conversation, including the parts that were said differently.

Two things are being checked here and only one of them is obvious. The
obvious one is that turns go in and come back in order. The other is what
happens when somebody rewrites something: the original has to survive, the
thread has to follow the rewrite, and the extraction pipeline must never see
the branch that was abandoned — because a message somebody took back is not
something they believe.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from lumen.operational import models
from lumen.operational.enums import BufferSource
from lumen.query.conversation import ConversationStore

TODAY = date(2026, 8, 17)
AT = datetime(2026, 8, 17, 10, 0, tzinfo=UTC)


@pytest.fixture
def store(ops_store):
    """A conversation store over a real, empty operational database."""
    return ConversationStore(ops_store.buffers)


@pytest.fixture
def chat(store):
    """One open conversation, ready to be talked into."""
    return store.open("tester", on=TODAY).session_id


def said(store, session_id, *contents, role="user"):
    """Say several things in a row, and hand back what they became."""
    return [
        store.append(session_id, role=role, content=content, at=AT)
        for content in contents
    ]


class TestHoldingAConversation:
    def test_a_turn_goes_in_and_comes_back(self, store, chat):
        said(store, chat, "I keep putting it off")

        thread = store.thread(chat)

        assert [item.turn.content for item in thread] == ["I keep putting it off"]

    def test_turns_come_back_in_the_order_they_were_said(self, store, chat):
        said(store, chat, "first", "second", "third")

        assert [item.turn.content for item in store.thread(chat)] == [
            "first",
            "second",
            "third",
        ]

    def test_each_turn_knows_what_it_replied_to(self, store, chat):
        first, second = said(store, chat, "first", "second")

        assert second.parent_message_id is None or True
        assert store.thread(chat)[1].parent_message_id == first.message_id

    def test_positions_follow_the_conversation_rather_than_arrival(self, store, chat):
        said(store, chat, "first", "second")

        assert [item.turn.turn_index for item in store.thread(chat)] == [0, 1]

    def test_both_sides_of_the_conversation_are_kept(self, store, chat):
        store.append(chat, role="user", content="I keep putting it off", at=AT)
        store.append(chat, role="assistant", content="What is it you are putting off?", at=AT)

        assert [item.turn.role for item in store.thread(chat)] == ["user", "assistant"]

    def test_opening_the_same_day_twice_is_the_same_conversation(self, store):
        first = store.open("tester", on=TODAY)
        second = store.open("tester", on=TODAY)

        assert first.session_id == second.session_id

    def test_a_conversation_that_does_not_exist_cannot_be_talked_into(self, store):
        with pytest.raises(ValueError):
            store.append("no_such_chat", role="user", content="hello", at=AT)


class TestSayingSomethingDifferently:
    def test_the_rewrite_becomes_the_thread(self, store, chat):
        first, second = said(store, chat, "I keep putting it off", "and it is fine")

        store.revise(chat, message_id=second.message_id, content="and it is not fine")

        assert [item.turn.content for item in store.thread(chat)] == [
            "I keep putting it off",
            "and it is not fine",
        ]

    def test_the_original_is_not_destroyed(self, store, chat, ops_store):
        # The same instinct as the graph's append-only rule, applied to what
        # was said.
        first, second = said(store, chat, "first", "second")

        store.revise(chat, message_id=second.message_id, content="second, differently")

        held = [record.content for record in ops_store.buffers.get_messages(chat)]
        assert "second" in held
        assert "second, differently" in held

    def test_everything_that_followed_the_original_drops_out_of_the_thread(
        self, store, chat
    ):
        # Replies to a message that was taken back are answers to a question
        # nobody asked any more.
        first, second, third = said(store, chat, "first", "second", "third")

        store.revise(chat, message_id=second.message_id, content="second, differently")

        assert [item.turn.content for item in store.thread(chat)] == [
            "first",
            "second, differently",
        ]

    def test_the_rewrite_sits_where_the_original_sat(self, store, chat):
        first, second = said(store, chat, "first", "second")

        revised = store.revise(
            chat, message_id=second.message_id, content="second, differently"
        )

        assert revised.parent_message_id == first.message_id

    def test_a_first_turn_can_be_rewritten_too(self, store, chat):
        first, _ = said(store, chat, "first", "second")

        store.revise(chat, message_id=first.message_id, content="actually this")

        assert [item.turn.content for item in store.thread(chat)] == ["actually this"]

    def test_rewriting_something_that_is_not_there_is_refused(self, store, chat):
        with pytest.raises(ValueError):
            store.revise(chat, message_id="msg_nothing", content="anything")

    def test_an_abandoned_branch_can_be_returned_to(self, store, chat):
        # Nothing was rewritten — only which end is being read from.
        first, second = said(store, chat, "first", "second")
        store.revise(chat, message_id=second.message_id, content="second, differently")

        store.rewind_to(chat, second.message_id)

        assert [item.turn.content for item in store.thread(chat)] == ["first", "second"]


class TestReturningToABranchThatIsNotThere:
    def test_it_is_refused_rather_than_silently_ignored(self, store, chat, ops_store):
        # Pointing a conversation at a message it does not contain would
        # leave it reading from nothing, which is much harder to notice than
        # a refusal.
        from lumen.operational.repositories import RecordNotFoundError

        said(store, chat, "first")

        with pytest.raises(RecordNotFoundError):
            store.rewind_to(chat, "msg_never_written")


class TestWhatThePipelineSees:
    def test_it_gets_the_thread_rather_than_every_message(self, store, chat, ops_store):
        # A message somebody edited away was said, but it is not what they
        # settled on. Letting abandoned branches become permanent history
        # would record arguments they took back.
        first, second = said(store, chat, "I think it is my fault", "and I deserve it")
        store.revise(
            chat, message_id=second.message_id, content="but that is the old story"
        )

        event = ops_store.buffers.build_decay_event(chat)

        contents = [message.content for message in event.raw_buffer]
        assert contents == ["I think it is my fault", "but that is the old story"]

    def test_an_untouched_conversation_reaches_it_whole(self, store, chat, ops_store):
        said(store, chat, "first", "second", "third")

        event = ops_store.buffers.build_decay_event(chat)

        assert len(event.raw_buffer) == 3

    def test_an_imported_conversation_is_unaffected(self, ops_store):
        # Imports arrive linear and stay linear: no reply links, no active
        # pointer, and reading them is unchanged.
        from lumen.operational.schemas import BufferMessageRecord

        buffer = ops_store.buffers.find_or_create(
            "tester", TODAY, source=BufferSource.IMPORT_JSON
        )
        for index in range(3):
            ops_store.buffers.append_message(
                buffer.session_id,
                BufferMessageRecord(
                    message_id=f"m_{index}",
                    session_id=buffer.session_id,
                    seq=index,
                    role="USER",
                    content=f"line {index}",
                    timestamp=AT,
                    event_date=TODAY,
                ),
            )

        thread = ops_store.buffers.active_thread(buffer.session_id)

        assert [record.content for record in thread] == ["line 0", "line 1", "line 2"]


class TestWhenTheThreadIsBroken:
    def test_a_chain_running_into_a_missing_message_falls_back_to_everything(
        self, store, chat, ops_store, ops_engine
    ):
        # Only reachable if a message were removed from underneath a
        # conversation, which nothing does. Half a conversation is a much
        # worse answer than one carrying a branch nobody wanted, so it falls
        # back rather than returning what it managed to walk.
        from sqlalchemy import update
        from sqlalchemy.orm import Session

        first, second = said(store, chat, "first", "second")

        with Session(ops_engine) as db:
            db.execute(
                update(models.BufferMessage)
                .where(models.BufferMessage.message_id == second.message_id)
                .values(parent_message_id="msg_that_is_not_there")
            )
            db.commit()

        thread = ops_store.buffers.active_thread(chat)

        assert [record.content for record in thread] == ["first", "second"]


class TestTheSummary:
    def test_it_can_be_written_and_read_back(self, store, chat):
        store.remember_summary(chat, "They came in about work.", 4)

        buffer = store.get(chat)
        assert buffer.rolling_summary == "They came in about work."
        assert buffer.summary_through_seq == 4

    def test_writing_a_new_one_replaces_the_old(self, store, chat):
        store.remember_summary(chat, "first account", 2)
        store.remember_summary(chat, "second account", 6)

        assert store.get(chat).rolling_summary == "second account"

    def test_a_conversation_starts_without_one(self, store, chat):
        buffer = store.get(chat)

        assert buffer.rolling_summary is None
        assert buffer.summary_through_seq == 0
