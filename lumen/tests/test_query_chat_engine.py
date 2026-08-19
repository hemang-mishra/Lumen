"""
One turn of a conversation, end to end.

The engine makes no decisions of its own — every piece it uses was finished
before it existed. What is tested here is the *order*, because the order is
the entire design: what is stored before anything can fail, what is fetched
before the reply starts, and what happens after the reply has gone out.

Run against real stores with scripted models, because the ordering guarantees
are about what survives a failure, and a stand-in store survives everything.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from lumen.config import AppConfig, ChatConfig, QueryConfig
from lumen.providers.errors import ProviderError
from lumen.providers.fake import FakeLLMProvider, FakeSpeechProvider
from lumen.query.chat import (
    ChatEngine,
    ContextReady,
    ReplyDelta,
    ReplyDone,
    SpokenReply,
    TurnAccepted,
    TurnFailed,
)
from lumen.query.chat.engine import VOICE
from lumen.query.conversation import ConversationStore
from lumen.query.formulation import QueryFormulator
from lumen.query.memory import ConversationMemory
from lumen.query.prompting import PersonaStore, PromptComposer
from lumen.query.session import SessionRegistry
from lumen.schemas.enums import ModelRole

USER = "tester"
NOW = datetime(2026, 8, 18, 10, 0, tzinfo=UTC)
REPLY = "That sounds like it took something out of you to do at all."


def a_reading(**overrides) -> str:
    """One scripted answer from the turn reader."""
    reply = {
        "triggers": [],
        "emotional_register": "STABLE",
        "named_entities": [],
        "confidence": 0.8,
        "critical_domain_opened": None,
    }
    reply.update(overrides)
    return json.dumps(reply)


class NoGraph:
    """A graph with nothing in it, which is all the ordering needs."""

    def list_era_tags(self, *, limit=50):
        return []

    def get_node(self, node_id):
        return None

    def find_nodes(self, node_types, **kwargs):
        return []


class NothingFound:
    """A retriever that answers every turn with nothing."""

    def __init__(self):
        self.asked = []

    def retrieve(self, signal, session):
        from lumen.query.retrieval.contracts import RetrievalBundle

        self.asked.append(signal.turn_index)
        return RetrievalBundle(
            session_id=signal.session_id, turn_index=signal.turn_index
        )


@pytest.fixture
def build(ops_store):
    """Build an engine over a real conversation store and scripted models."""

    def _build(
        *,
        replies=None,
        readings=None,
        speech=None,
        retriever=None,
        chat_config=None,
        break_after=None,
        personas=None,
    ):
        settings = chat_config or ChatConfig()
        memory = ConversationMemory(
            store=ConversationStore(ops_store.buffers),
            llm=FakeLLMProvider(["a summary of what was said"] * 20),
            config=settings,
        )
        return ChatEngine(
            formulator=QueryFormulator(
                llm=FakeLLMProvider(readings or [a_reading()] * 20),
                graph=NoGraph(),
                config=QueryConfig(),
            ),
            retriever=retriever or NothingFound(),
            composer=PromptComposer(config=settings),
            memory=memory,
            sessions=SessionRegistry(QueryConfig()),
            llm=FakeLLMProvider(
                replies or [REPLY] * 20,
                role=ModelRole.CONVERSATION,
                break_after=break_after,
            ),
            speech=speech,
            personas=personas,
            config=settings,
        )

    return _build


def run(engine, text: str, **kwargs) -> list:
    """Say one thing and collect everything that happened."""
    return list(engine.say(USER, text, at=kwargs.pop("at", NOW), **kwargs))


def only(events, kind) -> list:
    """Just the events of one sort."""
    return [event for event in events if isinstance(event, kind)]


class TestTheOrderOfATurn:
    def test_it_reports_each_step_as_it_happens(self, build):
        events = run(build(), "I went for a walk on my own today")

        assert [type(event).__name__ for event in events][:2] == [
            "TurnAccepted",
            "ContextReady",
        ]
        assert only(events, ReplyDelta)
        assert only(events, ReplyDone)

    def test_what_was_said_is_stored_before_anything_can_fail(self, build):
        """
        First on purpose. Everything after it can go wrong, and the person's
        own words should survive that — a turn that dies while the model is
        writing must not also lose the sentence that started it.
        """
        class Refuses(FakeLLMProvider):
            def _request_stream(self, **kwargs):
                raise ProviderError("no model today")

        engine = build()
        engine._llm = Refuses([], role=ModelRole.CONVERSATION)

        events = run(engine, "something I want kept")

        accepted = only(events, TurnAccepted)[0]
        thread = engine._memory.store.thread(accepted.session_id)
        assert [item.turn.content for item in thread] == ["something I want kept"]

    def test_the_reply_is_stored_once_it_finishes(self, build):
        engine = build()

        events = run(engine, "I went for a walk")

        done = only(events, ReplyDone)[0]
        thread = engine._memory.store.thread(
            only(events, TurnAccepted)[0].session_id
        )
        assert [item.turn.role for item in thread] == ["user", "assistant"]
        assert thread[-1].turn.content == REPLY
        assert done.text == REPLY

    def test_the_pieces_join_up_into_what_was_stored(self, build):
        engine = build()

        events = run(engine, "I went for a walk")

        streamed = "".join(event.text for event in only(events, ReplyDelta))
        assert streamed.strip() == only(events, ReplyDone)[0].text

    def test_nothing_happens_until_somebody_reads(self, build):
        engine = build()

        engine.say(USER, "prepared and then dropped", at=NOW)

        assert engine._retriever.asked == []

    def test_an_empty_turn_is_refused(self, build):
        with pytest.raises(ValueError):
            build().say(USER, "   ")


class TestWhatTheTurnReportsGathering:
    def test_it_names_how_the_person_sounds(self, build):
        engine = build(readings=[a_reading(emotional_register="REFLECTIVE")])

        gathered = only(run(engine, "I have been thinking about this"), ContextReady)[0]

        assert gathered.emotional_register == "REFLECTIVE"

    def test_it_reports_the_two_latencies_separately(self, build):
        """
        Reading a turn and fetching its history are budgeted separately and
        fail separately, so one number for both would hide which was slow.
        """
        gathered = only(run(build(), "I went for a walk"), ContextReady)[0]

        assert gathered.formulation_ms >= 0
        assert gathered.retrieval_ms >= 0

    def test_a_search_that_could_not_run_is_reported_as_one(self, build):
        from lumen.query.retrieval.contracts import (
            PassReport,
            RetrievalBundle,
        )
        from lumen.schemas.enums import RetrievalPass

        class Broken:
            def retrieve(self, signal, session):
                return RetrievalBundle(
                    session_id=signal.session_id,
                    turn_index=signal.turn_index,
                    passes=(
                        PassReport(
                            which=RetrievalPass.SEMANTIC,
                            ran=True,
                            failure="SearchUnavailable",
                        ),
                    ),
                )

        gathered = only(
            run(build(retriever=Broken()), "I went for a walk"), ContextReady
        )[0]

        assert gathered.search_failed is True


class TestWhenTheReplyBreaks:
    def test_what_was_already_said_is_kept(self, build):
        """
        The person read those words. Pretending they were never said would
        leave the stored conversation disagreeing with what is on their
        screen, and the next turn answered against a history missing half of
        it.
        """
        engine = build(break_after=2)

        events = run(engine, "I went for a walk")

        session_id = only(events, TurnAccepted)[0].session_id
        thread = engine._memory.store.thread(session_id)
        assert len(thread) == 2
        assert REPLY.startswith(thread[-1].turn.content)

    def test_it_says_how_much_had_been_said(self, build):
        events = run(build(break_after=2), "I went for a walk")

        failed = only(events, TurnFailed)[0]
        assert failed.reason == "reply_interrupted"
        assert failed.said

    def test_a_model_that_will_not_answer_at_all_fails_cleanly(self, build):
        class Refuses(FakeLLMProvider):
            def _request_stream(self, **kwargs):
                raise ProviderError("no model today")

        engine = build()
        engine._llm = Refuses([], role=ModelRole.CONVERSATION)

        events = run(engine, "I went for a walk")

        assert only(events, TurnFailed)[0].reason == "reply_failed"
        assert not only(events, ReplyDone)

    def test_a_reply_of_nothing_is_not_stored(self, build):
        engine = build(replies=["   "])

        events = run(engine, "I went for a walk")

        assert only(events, TurnFailed)[0].reason == "empty_reply"
        session_id = only(events, TurnAccepted)[0].session_id
        assert len(engine._memory.store.thread(session_id)) == 1


class TestSpeaking:
    def test_the_reply_comes_back_as_something_to_listen_to(self, build):
        engine = build(speech=FakeSpeechProvider())

        events = run(engine, "I went for a walk", speak=True)

        spoken = only(events, SpokenReply)[0]
        assert spoken.audio
        assert spoken.mime_type == "audio/wav"

    def test_it_is_said_after_the_words_not_instead_of_them(self, build):
        engine = build(speech=FakeSpeechProvider())

        events = run(engine, "I went for a walk", speak=True)

        kinds = [type(event).__name__ for event in events]
        assert kinds.index("ReplyDone") < kinds.index("SpokenReply")

    def test_nothing_is_spoken_unless_it_was_asked_for(self, build):
        engine = build(speech=FakeSpeechProvider())

        events = run(engine, "I went for a walk")

        assert not only(events, SpokenReply)

    def test_a_deployment_with_no_voice_still_talks(self, build):
        events = run(build(speech=None), "I went for a walk", speak=True)

        assert only(events, ReplyDone)
        assert not only(events, SpokenReply)

    def test_a_voice_that_fails_costs_the_audio_and_nothing_else(self, build):
        """
        The words are written, stored and already on the screen. The voice is
        the one part of a turn that can be missing without the turn failing.
        """

        class Mute(FakeSpeechProvider):
            def synthesize(self, text):
                raise ProviderError("the voice is out")

        events = run(build(speech=Mute()), "I went for a walk", speak=True)

        assert only(events, ReplyDone)
        assert not only(events, SpokenReply)
        assert not only(events, TurnFailed)

    def test_a_spoken_turn_is_recorded_as_spoken(self, build):
        """
        The pipeline cleans speech differently from typing, and has never had
        anything to read because nothing could speak.
        """
        engine = build()

        events = run(engine, "I went for a walk", modality=VOICE)

        session_id = only(events, TurnAccepted)[0].session_id
        stored = engine._memory.store._buffers.get_messages(session_id)
        assert stored[0].modality == "VOICE"


class TestAcrossDays:
    def test_crossing_midnight_writes_up_the_day_that_ended(self, build):
        """
        A short conversation never accumulates enough turns to be summarised
        on the usual cadence, so without forcing one at the boundary the
        continuity would work only for the days somebody talked a lot.
        """
        engine = build(chat_config=ChatConfig(recent_turns=1, summary_every=1))

        run(engine, "monday was hard", at=NOW)
        run(engine, "tuesday feels different", at=NOW + timedelta(days=1))

        monday = engine._memory.store.open(USER, on=NOW.date())
        assert monday.rolling_summary

    def test_today_opens_knowing_what_the_earlier_days_were_about(self, build):
        engine = build(chat_config=ChatConfig(recent_turns=1, summary_every=1))

        run(engine, "monday was hard", at=NOW)
        events = run(engine, "tuesday feels different", at=NOW + timedelta(days=1))

        assert only(events, ContextReady)[0].previous_days == 1

    def test_a_day_gets_its_own_conversation(self, build):
        engine = build()

        first = run(engine, "monday", at=NOW)
        second = run(engine, "tuesday", at=NOW + timedelta(days=1))

        assert (
            only(first, TurnAccepted)[0].session_id
            != only(second, TurnAccepted)[0].session_id
        )


class TestAfterTheReplyHasGoneOut:
    def test_the_summary_is_refreshed_once_the_turn_is_over(self, build):
        """
        Deliberately last. It is a model call, and nobody should wait on it
        to be answered — it is preparing for the turn after next.
        """
        engine = build(chat_config=ChatConfig(recent_turns=1, summary_every=1))

        events = run(engine, "the first thing")
        run(engine, "the second thing")

        session_id = only(events, TurnAccepted)[0].session_id
        assert engine._memory.store.get(session_id).rolling_summary

    def test_a_failed_refresh_does_not_cost_the_turn(self, build):
        engine = build(chat_config=ChatConfig(recent_turns=1, summary_every=1))
        engine._memory._llm = None

        events = run(engine, "the first thing")

        assert only(events, ReplyDone)


class TestWhenTheTidyingUpFails:
    """
    None of the housekeeping around a turn may cost the turn.

    Writing up a finished day and folding older turns into the summary both
    happen where nobody is waiting, and both are model calls. A failure in
    either costs the conversation some coherence later; it must never cost
    the person the answer they were waiting for.
    """

    def test_a_day_that_cannot_be_written_up_does_not_break_the_next_one(
        self, build, monkeypatch
    ):
        engine = build()
        run(engine, "monday", at=NOW)

        def broken(*args, **kwargs):
            raise RuntimeError("the store said no")

        monkeypatch.setattr(engine._memory, "refresh", broken)

        events = run(engine, "tuesday", at=NOW + timedelta(days=1))

        assert only(events, ReplyDone)

    def test_a_summary_that_cannot_be_written_does_not_cost_the_reply(
        self, build, monkeypatch
    ):
        engine = build()

        def broken(*args, **kwargs):
            raise RuntimeError("the store said no")

        monkeypatch.setattr(engine._memory, "refresh", broken)

        events = run(engine, "I went for a walk")

        assert only(events, ReplyDone)

    def test_a_break_before_any_words_stores_nothing_extra(self, build):
        """
        Nothing reached the person, so there is no half-reply to keep and the
        conversation should hold only what they said.
        """

        class BreaksImmediately(FakeLLMProvider):
            def _request_stream(self, **kwargs):
                raise ProviderError("gone before a word")

        engine = build()
        engine._llm = BreaksImmediately([], role=ModelRole.CONVERSATION)

        events = run(engine, "I went for a walk")

        session_id = only(events, TurnAccepted)[0].session_id
        assert len(engine._memory.store.thread(session_id)) == 1


class TestTheWordingTheTurnIsAnsweredWith:
    """
    Whether what a person wrote in their settings reaches the model.

    The mechanism is tested where it lives; what is checked here is the one
    join this file owns — that the engine looks the instruction up for the
    person who is actually speaking, and hands it to the composer rather than
    resolving it and dropping it.
    """

    def test_a_turn_uses_this_persons_own_wording(self, build, ops_store):
        personas = PersonaStore(settings=ops_store.settings)
        personas.save(USER, {"identity": "You are Ada. Be blunt."})

        prompt = _the_prompt(build(personas=personas))
        assert "You are Ada. Be blunt." in prompt

    def test_a_turn_without_a_store_uses_the_shipped_wording(self, build):
        assert "You are Lumen." in _the_prompt(build())

    def test_one_persons_wording_does_not_reach_another(self, build, ops_store):
        personas = PersonaStore(settings=ops_store.settings)
        personas.save("somebody-else", {"identity": "You are Ada."})

        assert "You are Ada." not in _the_prompt(build(personas=personas))

    def test_an_instruction_that_cannot_be_read_still_answers_the_turn(
        self, build, ops_store
    ):
        """
        A settings table that will not answer costs the wording, not the turn.

        Refusing to talk to somebody because a paragraph could not be read
        would be the wrong trade by a wide margin.
        """

        class Broken:
            def get(self, user_id, key):
                raise RuntimeError("no database")

            def set(self, user_id, key, value):
                raise RuntimeError("no database")

            def delete(self, user_id, key):
                raise RuntimeError("no database")

            def get_all(self, user_id):
                raise RuntimeError("no database")

        events = list(build(personas=PersonaStore(settings=Broken())).say(USER, "hello"))
        assert any(isinstance(event, ReplyDone) for event in events)


def _the_prompt(built) -> str:
    """
    The instructions one turn was actually answered with.

    Read off the scripted model rather than off the composer, because what
    is being checked is that the wording reached the call — resolving it and
    then not passing it on would look identical from anywhere earlier.
    """
    list(built.say(USER, "I keep putting this off."))
    spoken = [call for call in built._llm.calls if call.operation == "stream_text"]
    assert spoken, "the turn never reached the model"
    return spoken[-1].system_instruction or ""
