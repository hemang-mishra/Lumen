"""
What a day's conversation remembers, and what it forgets.

The interesting behaviour here is all about the boundary. A session is one
calendar day, so the questions worth asking are what happens at midnight,
what survives it, and what deliberately does not.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from lumen.config import QueryConfig
from lumen.query.session import ChatSession, SessionRegistry, make_session_id
from lumen.schemas.enums import Domain

MORNING = datetime(2026, 8, 16, 9, 0, tzinfo=UTC)
LATE = datetime(2026, 8, 16, 23, 58, tzinfo=UTC)
JUST_AFTER = datetime(2026, 8, 17, 0, 2, tzinfo=UTC)


class TestNamingASession:
    def test_a_session_is_named_for_the_person_and_the_day(self):
        assert make_session_id("hemang", MORNING.date()) == "hemang_2026_08_16"

    def test_a_labelled_conversation_keeps_its_label(self):
        assert make_session_id("hemang", MORNING.date(), "evening") == (
            "hemang_2026_08_16_evening"
        )

    def test_two_people_on_one_day_are_different_sessions(self):
        assert make_session_id("a", MORNING.date()) != make_session_id(
            "b", MORNING.date()
        )


class TestRememberingTurns:
    def test_turns_come_back_oldest_first(self, chat_session, make_turn):
        for index in range(3):
            chat_session.record_turn(make_turn(f"turn {index}", turn_index=index))

        assert [turn.content for turn in chat_session.recent_turns(3)] == [
            "turn 0",
            "turn 1",
            "turn 2",
        ]

    def test_only_the_last_few_are_asked_for(self, chat_session, make_turn):
        for index in range(5):
            chat_session.record_turn(make_turn(f"turn {index}", turn_index=index))

        assert [turn.content for turn in chat_session.recent_turns(2)] == [
            "turn 3",
            "turn 4",
        ]

    def test_asking_for_none_gives_none(self, chat_session, make_turn):
        chat_session.record_turn(make_turn())

        assert chat_session.recent_turns(0) == []

    def test_asking_for_a_negative_number_gives_none_rather_than_everything(
        self, chat_session, make_turn
    ):
        # Slicing would quietly turn this into the whole conversation, which
        # is the opposite of what a window of less than nothing should mean.
        chat_session.record_turn(make_turn())

        assert chat_session.recent_turns(-1) == []

    def test_a_long_day_drops_its_oldest_turns(self, make_turn):
        session = ChatSession(
            session_id="s",
            user_id="u",
            event_date=MORNING.date(),
            max_turns=3,
        )
        for index in range(6):
            session.record_turn(make_turn(f"turn {index}", turn_index=index))

        assert [turn.content for turn in session.recent_turns(10)] == [
            "turn 3",
            "turn 4",
            "turn 5",
        ]

    def test_numbering_does_not_restart_when_old_turns_are_dropped(self, make_turn):
        # The window forgets turns; the count must not, or two different
        # turns would end up carrying the same number.
        session = ChatSession(
            session_id="s", user_id="u", event_date=MORNING.date(), max_turns=2
        )
        for index in range(5):
            session.record_turn(make_turn(turn_index=index))

        assert session.next_turn_index() == 5
        assert session.turn_count == 5

    def test_a_fresh_session_starts_at_zero(self, chat_session):
        assert chat_session.next_turn_index() == 0

    def test_recording_a_turn_marks_the_session_active(self, chat_session, make_turn):
        chat_session.record_turn(make_turn(at=LATE))

        assert chat_session.last_activity_at == LATE


class TestOpeningSensitiveGround:
    def test_nothing_is_open_to_begin_with(self, chat_session):
        assert chat_session.unlocked_domains == ()
        assert not chat_session.is_unlocked(Domain.SELF_CONCEPT)

    def test_what_the_person_opens_stays_open(self, chat_session):
        chat_session.unlock(Domain.SELF_CONCEPT)

        assert chat_session.is_unlocked(Domain.SELF_CONCEPT)

    def test_opening_the_same_ground_twice_changes_nothing(self, chat_session):
        chat_session.unlock(Domain.RELATIONAL)
        chat_session.unlock(Domain.RELATIONAL)

        assert chat_session.unlocked_domains == (Domain.RELATIONAL,)

    def test_open_ground_comes_back_in_a_stable_order(self, chat_session):
        chat_session.unlock(Domain.SELF_CONCEPT)
        chat_session.unlock(Domain.CAREER)

        assert chat_session.unlocked_domains == (Domain.CAREER, Domain.SELF_CONCEPT)


class TestTheEraVocabulary:
    def test_it_is_unfetched_until_somebody_fetches_it(self, chat_session):
        assert chat_session.era_vocabulary is None

    def test_it_is_kept_once_fetched(self, chat_session):
        chat_session.remember_era_vocabulary(("high school", "first job"))

        assert chat_session.era_vocabulary == ("high school", "first job")

    def test_an_empty_answer_is_still_an_answer(self, chat_session):
        # None means nobody has looked; an empty list means somebody looked
        # and there was nothing. Looking again every turn would cost a
        # database read per turn for the same nothing.
        chat_session.remember_era_vocabulary(())

        assert chat_session.era_vocabulary == ()


class TestTheDayBoundary:
    def test_the_same_day_gives_the_same_session(self):
        registry = SessionRegistry()

        first = registry.open("u", at=MORNING)
        second = registry.open("u", at=LATE)

        assert first is second

    def test_four_minutes_later_across_midnight_is_a_new_session(self, make_turn):
        registry = SessionRegistry()
        yesterday = registry.open("u", at=LATE)
        yesterday.record_turn(make_turn())

        today = registry.open("u", at=JUST_AFTER)

        assert today is not yesterday
        assert today.session_id == "u_2026_08_17"
        assert today.turn_count == 0

    def test_what_was_opened_yesterday_is_locked_again_today(self):
        registry = SessionRegistry()
        registry.open("u", at=LATE).unlock(Domain.SELF_CONCEPT)

        assert registry.open("u", at=JUST_AFTER).unlocked_domains == ()

    def test_two_labelled_conversations_on_one_day_stay_apart(self):
        registry = SessionRegistry()

        morning = registry.open("u", at=MORNING)
        evening = registry.open("u", at=MORNING, label="evening")

        assert morning is not evening
        assert evening.session_id.endswith("_evening")

    def test_two_people_never_share_a_session(self):
        registry = SessionRegistry()

        assert registry.open("a", at=MORNING) is not registry.open("b", at=MORNING)

    def test_a_session_can_be_found_by_name(self):
        registry = SessionRegistry()
        opened = registry.open("u", at=MORNING)

        assert registry.get(opened.session_id) is opened

    def test_a_name_nobody_has_finds_nothing(self):
        assert SessionRegistry().get("u_1999_01_01") is None

    def test_closing_forgets_the_session(self):
        registry = SessionRegistry()
        opened = registry.open("u", at=MORNING)

        registry.close(opened.session_id)

        assert registry.get(opened.session_id) is None

    def test_closing_something_already_gone_is_not_an_error(self):
        SessionRegistry().close("u_1999_01_01")

    def test_closing_everything_leaves_nothing(self):
        registry = SessionRegistry()
        registry.open("a", at=MORNING)
        registry.open("b", at=MORNING)

        registry.close_all()

        assert registry.get("a_2026_08_16") is None

    def test_the_turn_ceiling_comes_from_configuration(self):
        registry = SessionRegistry(QueryConfig(session_max_turns=7))

        assert registry.open("u", at=MORNING).max_turns == 7

    @pytest.mark.parametrize("ceiling", [0, -5])
    def test_a_nonsense_ceiling_still_keeps_one_turn(self, ceiling, make_turn):
        # A window of zero would mean recording a turn and immediately
        # losing it, which reads as a bug in the caller rather than a
        # setting worth honouring exactly.
        session = ChatSession(
            session_id="s", user_id="u", event_date=MORNING.date(), max_turns=ceiling
        )
        session.record_turn(make_turn())

        assert len(session.recent_turns(5)) == 1
