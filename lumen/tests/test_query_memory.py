"""
Keeping hold of a long conversation.

The behaviour worth pinning is the trade at the centre of it: recent turns
word for word, everything older compressed, and the compression happening
off the critical path rather than while somebody waits.

The failure to guard against is subtler than losing the summary. It is
sending the summary *and* the turns it describes, so the assistant reads the
same stretch of conversation twice in two different voices.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from lumen.config import ChatConfig
from lumen.providers.errors import ProviderError
from lumen.providers.fake import FakeLLMProvider
from lumen.query.conversation import ConversationStore
from lumen.query.memory import ConversationMemory

TODAY = date(2026, 8, 17)
AT = datetime(2026, 8, 17, 10, 0, tzinfo=UTC)


@pytest.fixture
def store(ops_store):
    return ConversationStore(ops_store.buffers)


@pytest.fixture
def chat(store):
    return store.open("tester", on=TODAY).session_id


@pytest.fixture
def remember(store):
    """Build a memory over a scripted summariser."""

    def _build(script=None, **settings):
        return ConversationMemory(
            store=store,
            llm=FakeLLMProvider(script) if script is not None else None,
            config=ChatConfig(**settings),
        )

    return _build


def talk(store, chat, count, *, start=0):
    """Say a number of things, alternating sides the way a chat does."""
    for index in range(start, start + count):
        store.append(
            chat,
            role="user" if index % 2 == 0 else "assistant",
            content=f"turn {index}",
            at=AT,
        )


class TestWhatTheAssistantSees:
    def test_a_short_conversation_arrives_whole(self, store, chat, remember):
        talk(store, chat, 4)

        recalled = remember().recall(chat)

        assert [turn.content for turn in recalled.turns] == [
            "turn 0",
            "turn 1",
            "turn 2",
            "turn 3",
        ]

    def test_a_long_one_arrives_as_its_recent_part(self, store, chat, remember):
        talk(store, chat, 20)

        recalled = remember(recent_turns=5).recall(chat)

        assert len(recalled.turns) == 5
        assert recalled.turns[-1].content == "turn 19"

    def test_how_much_was_left_out_is_visible(self, store, chat, remember):
        talk(store, chat, 20)

        recalled = remember(recent_turns=5).recall(chat)

        assert recalled.total_turns == 20

    def test_an_empty_conversation_says_so(self, store, chat, remember):
        assert remember().recall(chat).is_empty is True

    def test_a_conversation_that_does_not_exist_is_empty_rather_than_a_failure(
        self, remember
    ):
        # "This is the first thing anybody has said" is an ordinary state of
        # a chat, not an error.
        assert remember().recall("no_such_chat").is_empty is True


class TestTheSummary:
    def test_it_is_sent_once_there_is_conversation_it_covers(
        self, store, chat, remember
    ):
        talk(store, chat, 20)
        store.remember_summary(chat, "They came in about work.", 10)

        recalled = remember(recent_turns=5).recall(chat)

        assert recalled.summary == "They came in about work."

    def test_it_is_not_sent_when_the_whole_chat_is_already_going_word_for_word(
        self, store, chat, remember
    ):
        # Otherwise the assistant reads the same stretch twice, in two
        # different voices.
        talk(store, chat, 3)
        store.remember_summary(chat, "They came in about work.", 2)

        recalled = remember(recent_turns=12).recall(chat)

        assert recalled.summary is None

    def test_recalling_costs_no_model_call(self, store, chat):
        # It runs on every single turn, so it has to be cheap.
        talk(store, chat, 20)
        llm = FakeLLMProvider([])

        ConversationMemory(
            store=store, llm=llm, config=ChatConfig(recent_turns=5)
        ).recall(chat)

        assert llm.calls == []


class TestWritingItUp:
    def test_enough_new_material_gets_summarised(self, store, chat, remember):
        talk(store, chat, 20)

        wrote = remember(["they have been talking about work"], recent_turns=5,
                         summary_every=4).refresh(chat)

        assert wrote is True
        assert store.get(chat).rolling_summary == "they have been talking about work"

    def test_how_far_it_reaches_is_recorded(self, store, chat, remember):
        # So the next refresh reads only what has been said since, rather
        # than the whole conversation again.
        talk(store, chat, 20)

        remember(["an account"], recent_turns=5, summary_every=4).refresh(chat)

        assert store.get(chat).summary_through_seq == 14

    def test_a_short_conversation_is_not_worth_a_call(self, store, chat, remember):
        talk(store, chat, 6)
        llm = FakeLLMProvider([])
        memory = ConversationMemory(
            store=store, llm=llm, config=ChatConfig(recent_turns=12, summary_every=4)
        )

        assert memory.refresh(chat) is False
        assert llm.calls == []

    def test_too_little_new_material_waits(self, store, chat, remember):
        talk(store, chat, 14)

        assert remember(["an account"], recent_turns=12, summary_every=8).refresh(
            chat
        ) is False

    def test_but_it_can_be_asked_for_anyway(self, store, chat, remember):
        talk(store, chat, 14)

        assert remember(["an account"], recent_turns=12, summary_every=8).refresh(
            chat, force=True
        ) is True

    def test_the_previous_account_is_folded_in_rather_than_replaced_blindly(
        self, store, chat
    ):
        # What keeps a long chat costing the same as a short one.
        talk(store, chat, 20)
        store.remember_summary(chat, "the first half was about work", 8)
        llm = FakeLLMProvider(["a merged account"])

        ConversationMemory(
            store=store,
            llm=llm,
            config=ChatConfig(recent_turns=5, summary_every=2),
        ).refresh(chat)

        assert "the first half was about work" in llm.calls[0].prompt

    def test_only_the_turns_since_the_last_account_are_sent(self, store, chat):
        talk(store, chat, 20)
        store.remember_summary(chat, "the first half", 8)
        llm = FakeLLMProvider(["a merged account"])

        ConversationMemory(
            store=store,
            llm=llm,
            config=ChatConfig(recent_turns=5, summary_every=2),
        ).refresh(chat)

        assert "turn 3" not in llm.calls[0].prompt
        assert "turn 12" in llm.calls[0].prompt


class TestWhenTheSummaryCannotBeWritten:
    def test_a_model_that_fails_leaves_the_old_account_standing(
        self, store, chat
    ):
        talk(store, chat, 20)
        store.remember_summary(chat, "the account so far", 4)

        class Broken(FakeLLMProvider):
            def generate_text(self, *args, **kwargs):
                raise ProviderError("no model")

        wrote = ConversationMemory(
            store=store, llm=Broken([]), config=ChatConfig(recent_turns=5, summary_every=2)
        ).refresh(chat)

        assert wrote is False
        assert store.get(chat).rolling_summary == "the account so far"

    def test_an_empty_answer_is_not_stored(self, store, chat, remember):
        talk(store, chat, 20)

        assert remember(["   "], recent_turns=5, summary_every=2).refresh(chat) is False

    def test_no_model_at_all_is_not_a_failure(self, store, chat, remember):
        # A deployment without one still holds a conversation. It just stops
        # compressing the older part of it.
        talk(store, chat, 20)

        assert remember(None, recent_turns=5, summary_every=2).refresh(chat) is False

    def test_a_conversation_that_does_not_exist_writes_nothing(self, remember):
        assert remember(["anything"]).refresh("no_such_chat") is False
