"""
Reading one turn, end to end.

The order things happen in is most of what is tested here, because it is
where the design lives: the two checks that run before any model is asked,
the deadline that the turn may not exceed, and the fact that the crisis
reading throws away work that had already been done.
"""

from __future__ import annotations

import json
import time

import pytest

from lumen.config import QueryConfig
from lumen.providers.errors import ProviderError, ProviderTimeoutError
from lumen.providers.fake import FakeLLMProvider
from lumen.query.formulation.deadline import DeadlineExceeded, DeadlineRunner
from lumen.schemas.enums import (
    Domain,
    EmotionalRegister,
    FormulationPath,
    TriggerType,
)


class StubGraph:
    """
    A graph that answers the three questions grounding asks.

    Kept deliberately simple: what grounding does with real answers is
    covered against a real database elsewhere, and what is being checked
    here is the order of the steps around it.
    """

    def __init__(self, *, eras=("high school",), people=(), open_loops=False):
        self.eras = list(eras)
        self.people = set(people)
        self.open_loops = open_loops

    def list_era_tags(self, *, limit=50):
        return self.eras[:limit]

    def get_node(self, node_id):
        return {"node_id": node_id} if node_id in self.people else None

    def find_nodes(self, node_types, **kwargs):
        return [{"node_id": "loop_001"}] if self.open_loops else []


class SlowProvider:
    """A model that takes longer than any turn is willing to wait."""

    provider_name = "slow"
    model_name = "slow-model"

    def __init__(self, delay: float = 5.0) -> None:
        self.delay = delay
        self.started = 0

    def generate_structured(self, prompt, response_model, **kwargs):
        self.started += 1
        time.sleep(self.delay)
        raise AssertionError("nobody should still be waiting for this")


class TestTheChecksBeforeTheModel:
    def test_a_distress_phrase_ends_the_turn_immediately(
        self, make_formulator, make_turn, chat_session
    ):
        llm = FakeLLMProvider([])
        formulator = make_formulator(StubGraph(), llm=llm)

        signal = formulator.formulate(make_turn("honestly I just want to die"), chat_session)

        assert signal.emotional_register is EmotionalRegister.CRISIS
        assert signal.formulation_path is FormulationPath.SAFETY_FLOOR
        assert not signal.should_retrieve
        # The outcome was already fixed, so paying for a call would only
        # have delayed it.
        assert llm.calls == []

    def test_an_acknowledgement_ends_the_turn_immediately(
        self, make_formulator, make_turn, chat_session
    ):
        llm = FakeLLMProvider([])
        formulator = make_formulator(StubGraph(), llm=llm)

        signal = formulator.formulate(make_turn("go on"), chat_session)

        assert signal.formulation_path is FormulationPath.ACKNOWLEDGEMENT
        assert signal.emotional_register is EmotionalRegister.STABLE
        assert llm.calls == []

    def test_the_floor_is_checked_before_the_acknowledgement_list(
        self, make_formulator, make_turn, chat_session
    ):
        # Nothing currently sits in both lists, and this is what would notice
        # if something ever did.
        formulator = make_formulator(StubGraph(), llm=FakeLLMProvider([]))

        signal = formulator.formulate(make_turn("no, I want to die"), chat_session)

        assert signal.formulation_path is FormulationPath.SAFETY_FLOOR


class TestAskingTheModel:
    def test_a_grounded_reading_comes_back_whole(
        self, make_formulator, make_turn, make_reply, chat_session
    ):
        reply = make_reply(
            triggers=[
                {
                    "trigger_type": "PATTERN_MENTION",
                    "domain": "SELF_CONCEPT",
                    "keywords": ["putting off", "avoidance"],
                }
            ],
            register="REFLECTIVE",
            confidence=0.87,
        )
        formulator = make_formulator(StubGraph(), script=[reply])

        signal = formulator.formulate(make_turn(), chat_session)

        assert signal.formulation_path is FormulationPath.CLASSIFIED
        assert signal.trigger_types == (TriggerType.PATTERN_MENTION,)
        assert signal.retrieval_triggers[0].domain is Domain.SELF_CONCEPT
        assert signal.emotional_register is EmotionalRegister.REFLECTIVE
        assert signal.query_formulation_confidence == 0.87
        assert signal.should_retrieve

    def test_a_reading_with_nothing_in_it_asks_for_nothing(
        self, make_formulator, make_turn, make_reply, chat_session
    ):
        formulator = make_formulator(StubGraph(), script=[make_reply()])

        signal = formulator.formulate(
            make_turn("what time did we start earlier?"), chat_session
        )

        assert signal.formulation_path is FormulationPath.CLASSIFIED
        assert not signal.should_retrieve

    def test_the_model_is_shown_the_eras_this_history_uses(
        self, make_formulator, make_turn, make_reply, chat_session
    ):
        llm = FakeLLMProvider([make_reply()])
        formulator = make_formulator(
            StubGraph(eras=("high school", "first job")), llm=llm
        )

        formulator.formulate(make_turn(), chat_session)

        assert "high school, first job" in llm.calls[0].prompt

    def test_a_history_with_no_eras_says_so_rather_than_leaving_a_gap(
        self, make_formulator, make_turn, make_reply, chat_session
    ):
        # An empty list reads as an invitation to invent one, and an invented
        # era is a lookup that can never match.
        llm = FakeLLMProvider([make_reply()])
        formulator = make_formulator(StubGraph(eras=()), llm=llm)

        formulator.formulate(make_turn(), chat_session)

        assert "never return an era" in llm.calls[0].prompt

    def test_the_model_sees_the_turns_before_this_one(
        self, make_formulator, make_turn, make_reply, chat_session
    ):
        llm = FakeLLMProvider([make_reply(), make_reply()])
        formulator = make_formulator(StubGraph(), llm=llm)
        chat_session.record_turn(
            make_turn("I used to think I was the problem", turn_index=0)
        )

        formulator.formulate(
            make_turn("I don't feel that anymore", turn_index=1), chat_session
        )

        prompt = llm.calls[0].prompt
        assert "I used to think I was the problem" in prompt
        assert "[CLASSIFY]: I don't feel that anymore" in prompt

    def test_only_the_newest_turn_is_marked_for_classifying(
        self, make_formulator, make_turn, make_reply, chat_session
    ):
        llm = FakeLLMProvider([make_reply()])
        formulator = make_formulator(StubGraph(), llm=llm)
        chat_session.record_turn(make_turn("earlier", turn_index=0))

        formulator.formulate(make_turn("now", turn_index=1), chat_session)

        assert llm.calls[0].prompt.count("[CLASSIFY]") == 1

    def test_the_window_is_configurable(
        self, make_formulator, make_turn, make_reply, chat_session
    ):
        llm = FakeLLMProvider([make_reply()])
        formulator = make_formulator(
            StubGraph(), llm=llm, config=QueryConfig(formulation_context_turns=2)
        )
        for index in range(4):
            chat_session.record_turn(make_turn(f"old {index}", turn_index=index))

        formulator.formulate(make_turn("now", turn_index=4), chat_session)

        prompt = llm.calls[0].prompt
        assert "old 3" in prompt
        assert "old 2" not in prompt


class TestWhenTheModelDoesNotAnswer:
    def test_a_slow_model_costs_the_turn_nothing_beyond_its_budget(
        self, make_formulator, make_turn, chat_session
    ):
        provider = SlowProvider(delay=2.0)
        formulator = make_formulator(
            StubGraph(),
            llm=provider,
            config=QueryConfig(formulation_timeout_seconds=0.05),
        )

        started = time.perf_counter()
        signal = formulator.formulate(make_turn(), chat_session)
        waited = time.perf_counter() - started

        assert signal.formulation_path is FormulationPath.TIMED_OUT
        assert not signal.should_retrieve
        assert waited < 1.0

    def test_a_failing_model_is_reported_apart_from_a_slow_one(
        self, make_formulator, make_turn, chat_session
    ):
        class Failing:
            provider_name = "failing"
            model_name = "failing-model"

            def generate_structured(self, prompt, response_model, **kwargs):
                raise ProviderTimeoutError("the vendor gave up")

        formulator = make_formulator(StubGraph(), llm=Failing())

        signal = formulator.formulate(make_turn(), chat_session)

        assert signal.formulation_path is FormulationPath.CALL_FAILED

    def test_an_unreadable_answer_asks_for_nothing(
        self, make_formulator, make_turn, chat_session
    ):
        formulator = make_formulator(StubGraph(), script=["this is not json at all"])

        signal = formulator.formulate(make_turn(), chat_session)

        assert signal.formulation_path is FormulationPath.CALL_FAILED
        assert not signal.should_retrieve

    def test_an_answer_of_the_wrong_shape_asks_for_nothing(
        self, make_formulator, make_turn, chat_session
    ):
        formulator = make_formulator(
            StubGraph(), script=[json.dumps({"triggers": "not a list"})]
        )

        signal = formulator.formulate(make_turn(), chat_session)

        assert signal.formulation_path is FormulationPath.CALL_FAILED

    def test_extra_fields_in_an_answer_are_ignored_rather_than_fatal(
        self, make_formulator, make_turn, chat_session
    ):
        formulator = make_formulator(
            StubGraph(),
            script=[
                json.dumps(
                    {
                        "triggers": [{"trigger_type": "SOMATIC_MARKER"}],
                        "emotional_register": "VULNERABLE",
                        "reasoning": "a field nobody asked for",
                    }
                )
            ],
        )

        signal = formulator.formulate(make_turn(), chat_session)

        assert signal.trigger_types == (TriggerType.SOMATIC_MARKER,)

    def test_every_failure_still_records_the_turn(
        self, make_formulator, make_turn, chat_session
    ):
        # A turn that was skipped was still said, and the turn after it may
        # only make sense against it.
        formulator = make_formulator(StubGraph(), script=["not json"])

        formulator.formulate(make_turn(), chat_session)

        assert chat_session.turn_count == 1


class TestWhenSomebodyIsInCrisis:
    def test_the_model_may_raise_a_turn_to_a_crisis(
        self, make_formulator, make_turn, make_reply, chat_session
    ):
        formulator = make_formulator(
            StubGraph(), script=[make_reply(register="CRISIS")]
        )

        signal = formulator.formulate(
            make_turn("everything is falling apart and I can't hold it"), chat_session
        )

        assert signal.emotional_register is EmotionalRegister.CRISIS

    def test_reasons_found_are_thrown_away_rather_than_acted_on(
        self, make_formulator, make_turn, make_reply, chat_session
    ):
        formulator = make_formulator(
            StubGraph(),
            script=[
                make_reply(
                    triggers=[{"trigger_type": "IDENTITY_STATEMENT"}], register="CRISIS"
                )
            ],
        )

        signal = formulator.formulate(make_turn("I am nothing"), chat_session)

        assert not signal.should_retrieve
        # The difference between "there was nothing" and "now is not the
        # time" is the whole reason this flag exists.
        assert signal.suppressed_by_crisis

    def test_finding_nothing_is_not_recorded_as_suppression(
        self, make_formulator, make_turn, make_reply, chat_session
    ):
        formulator = make_formulator(
            StubGraph(), script=[make_reply(register="CRISIS")]
        )

        signal = formulator.formulate(make_turn("I am nothing"), chat_session)

        assert not signal.suppressed_by_crisis

    def test_ground_the_person_opened_stays_open_through_a_crisis(
        self, make_formulator, make_turn, make_reply, chat_session
    ):
        # They did open it. Clearing that would make tomorrow's reading
        # discover it again, and the crisis suppresses this turn's lookup,
        # not the fact that the subject is now on the table.
        formulator = make_formulator(
            StubGraph(),
            script=[
                make_reply(register="CRISIS", critical_domain_opened="SELF_CONCEPT")
            ],
        )

        signal = formulator.formulate(make_turn("I am nothing"), chat_session)

        assert chat_session.is_unlocked(Domain.SELF_CONCEPT)
        assert signal.unlocked_domains == (Domain.SELF_CONCEPT,)


class TestOpeningSensitiveGround:
    def test_what_the_person_opens_is_remembered(
        self, make_formulator, make_turn, make_reply, chat_session
    ):
        formulator = make_formulator(
            StubGraph(), script=[make_reply(critical_domain_opened="RELATIONAL")]
        )

        signal = formulator.formulate(make_turn(), chat_session)

        assert signal.critical_domain_opened is Domain.RELATIONAL
        assert chat_session.is_unlocked(Domain.RELATIONAL)

    def test_it_stays_open_for_later_turns(
        self, make_formulator, make_turn, make_reply, chat_session
    ):
        formulator = make_formulator(
            StubGraph(),
            script=[
                make_reply(critical_domain_opened="RELATIONAL"),
                make_reply(),
            ],
        )
        formulator.formulate(make_turn(turn_index=0), chat_session)

        later = formulator.formulate(make_turn(turn_index=1), chat_session)

        assert later.critical_domain_opened is None
        assert later.unlocked_domains == (Domain.RELATIONAL,)

    def test_an_area_that_is_not_real_opens_nothing(
        self, make_formulator, make_turn, make_reply, chat_session
    ):
        formulator = make_formulator(
            StubGraph(), script=[make_reply(critical_domain_opened="adolescent_trauma")]
        )

        signal = formulator.formulate(make_turn(), chat_session)

        assert signal.critical_domain_opened is None
        assert signal.unlocked_domains == ()


class TestKeepingTheAnswerHonest:
    def test_only_a_few_reasons_survive_one_turn(
        self, make_formulator, make_turn, make_reply, chat_session
    ):
        formulator = make_formulator(
            StubGraph(people={"person_alex"}, open_loops=True),
            script=[
                make_reply(
                    triggers=[
                        {"trigger_type": "SOMATIC_MARKER"},
                        {"trigger_type": "IDENTITY_STATEMENT"},
                        {"trigger_type": "NAMED_PERSON", "people": ["Alex"]},
                        {"trigger_type": "OPEN_LOOP_MATCH"},
                        {"trigger_type": "HISTORICAL_ERA", "era": "high school"},
                    ]
                )
            ],
        )

        signal = formulator.formulate(make_turn(), chat_session)

        assert len(signal.retrieval_triggers) == 3
        # The exact ones are kept, because they are both cheaper to look up
        # and certain to match something.
        assert signal.trigger_types == (
            TriggerType.NAMED_PERSON,
            TriggerType.HISTORICAL_ERA,
            TriggerType.OPEN_LOOP_MATCH,
        )

    def test_how_many_survive_is_configurable(
        self, make_formulator, make_turn, make_reply, chat_session
    ):
        formulator = make_formulator(
            StubGraph(),
            config=QueryConfig(max_triggers_per_turn=1),
            script=[
                make_reply(
                    triggers=[
                        {"trigger_type": "PATTERN_MENTION"},
                        {"trigger_type": "SOMATIC_MARKER"},
                    ]
                )
            ],
        )

        signal = formulator.formulate(make_turn(), chat_session)

        assert len(signal.retrieval_triggers) == 1

    def test_a_reason_naming_somebody_unknown_never_leaves(
        self, make_formulator, make_turn, make_reply, chat_session
    ):
        formulator = make_formulator(
            StubGraph(people=set()),
            script=[
                make_reply(
                    triggers=[{"trigger_type": "NAMED_PERSON", "people": ["Nobody"]}],
                    named_entities=["Nobody"],
                )
            ],
        )

        signal = formulator.formulate(make_turn(), chat_session)

        assert not signal.should_retrieve
        # The raw name is still reported, because a name the graph has never
        # heard is worth being able to see.
        assert signal.named_entities_mentioned == ("Nobody",)

    @pytest.mark.parametrize(
        "given,expected", [(1.7, 1.0), (-0.4, 0.0), (0.5, 0.5)]
    )
    def test_confidence_is_held_to_the_range_it_is_defined_over(
        self, make_formulator, make_turn, make_reply, chat_session, given, expected
    ):
        formulator = make_formulator(StubGraph(), script=[make_reply(confidence=given)])

        signal = formulator.formulate(make_turn(), chat_session)

        assert signal.query_formulation_confidence == expected

    @pytest.mark.parametrize("said", ["", "PANICKED", "stable", "  REFLECTIVE  "])
    def test_a_register_nobody_recognises_reads_as_ordinary(
        self, make_formulator, make_turn, make_reply, chat_session, said
    ):
        formulator = make_formulator(StubGraph(), script=[make_reply(register=said)])

        signal = formulator.formulate(make_turn(), chat_session)

        assert signal.emotional_register in {
            EmotionalRegister.STABLE,
            EmotionalRegister.REFLECTIVE,
        }

    def test_wanting_to_retrieve_always_matches_having_a_reason_to(
        self, make_formulator, make_turn, make_reply, chat_session
    ):
        formulator = make_formulator(
            StubGraph(), script=[make_reply(triggers=[{"trigger_type": "PATTERN_MENTION"}])]
        )

        signal = formulator.formulate(make_turn(), chat_session)

        assert signal.should_retrieve is bool(signal.retrieval_triggers)


class TestWhatIsRecorded:
    def test_every_turn_leaves_one_line(
        self, make_formulator, make_turn, make_reply, chat_session, captured_logs
    ):
        formulator = make_formulator(StubGraph(), script=[make_reply()])

        formulator.formulate(make_turn(), chat_session)

        lines = [line for line in captured_logs if line["msg"] == "a turn was read"]
        assert len(lines) == 1
        assert lines[0]["path"] == "CLASSIFIED"

    def test_the_line_says_which_reasons_survived(
        self, make_formulator, make_turn, make_reply, chat_session, captured_logs
    ):
        # A reading that quietly said "nothing to look up" to everything
        # would show up here and nowhere else.
        formulator = make_formulator(
            StubGraph(), script=[make_reply(triggers=[{"trigger_type": "SOMATIC_MARKER"}])]
        )

        formulator.formulate(make_turn(), chat_session)

        line = next(l for l in captured_logs if l["msg"] == "a turn was read")
        assert line["triggers"] == ["SOMATIC_MARKER"]

    def test_the_turn_reader_reports_how_long_it_took(
        self, make_formulator, make_turn, make_reply, chat_session
    ):
        formulator = make_formulator(StubGraph(), script=[make_reply()])

        signal = formulator.formulate(make_turn(), chat_session)

        assert signal.latency_ms >= 0

    def test_the_signal_names_the_session_and_turn_it_belongs_to(
        self, make_formulator, make_turn, make_reply, chat_session
    ):
        formulator = make_formulator(StubGraph(), script=[make_reply()])

        signal = formulator.formulate(make_turn(turn_index=4), chat_session)

        assert signal.session_id == chat_session.session_id
        assert signal.turn_index == 4

    def test_a_read_turn_becomes_part_of_the_conversation(
        self, make_formulator, make_turn, make_reply, chat_session
    ):
        formulator = make_formulator(StubGraph(), script=[make_reply()])

        formulator.formulate(make_turn("something worth remembering"), chat_session)

        assert chat_session.recent_turns(1)[0].content == "something worth remembering"


class TestTheDeadlineOnItsOwn:
    def test_work_that_finishes_in_time_comes_straight_back(self):
        runner = DeadlineRunner(max_workers=1, name="test")
        try:
            assert runner.run(lambda: 41 + 1, timeout_seconds=5) == 42
        finally:
            runner.close()

    def test_work_that_runs_long_raises_rather_than_blocking(self):
        runner = DeadlineRunner(max_workers=1, name="test")
        try:
            with pytest.raises(DeadlineExceeded):
                runner.run(lambda: time.sleep(3), timeout_seconds=0.05)
        finally:
            runner.close()

    def test_a_failure_inside_the_work_comes_back_as_itself(self):
        # A model that failed and a model that was slow need different
        # answers, so they must not arrive as the same exception.
        runner = DeadlineRunner(max_workers=1, name="test")

        def explode():
            raise ProviderError("the vendor refused")

        try:
            with pytest.raises(ProviderError):
                runner.run(explode, timeout_seconds=5)
        finally:
            runner.close()

    def test_a_closed_pool_reports_a_missed_deadline(self):
        runner = DeadlineRunner(max_workers=1, name="test")
        runner.close()

        with pytest.raises(DeadlineExceeded):
            runner.run(lambda: 1, timeout_seconds=5)

    def test_the_trace_identifier_survives_the_hop_to_another_thread(
        self, bound_trace
    ):
        from lumen.observability.trace import get_trace_id

        runner = DeadlineRunner(max_workers=1, name="test")
        try:
            assert runner.run(get_trace_id, timeout_seconds=5) == get_trace_id()
        finally:
            runner.close()

    def test_a_reader_that_made_its_own_pool_closes_it(self):
        from lumen.query.formulation import QueryFormulator

        formulator = QueryFormulator(llm=FakeLLMProvider([]), graph=StubGraph())
        formulator.close()

        with pytest.raises(DeadlineExceeded):
            formulator._runner.run(lambda: 1, timeout_seconds=1)

    def test_a_reader_given_a_pool_does_not_close_it(self):
        from lumen.query.formulation import QueryFormulator

        runner = DeadlineRunner(max_workers=1, name="test")
        try:
            QueryFormulator(
                llm=FakeLLMProvider([]), graph=StubGraph(), runner=runner
            ).close()

            assert runner.run(lambda: "still working", timeout_seconds=5) == (
                "still working"
            )
        finally:
            runner.close()


class TestTheSpecsOwnExamples:
    """
    The sentences the design was written around.

    These check the plumbing on real wording rather than the model's
    judgement, which is scripted here. Whether a real model reads these
    sentences the way the design assumes is a question about prompts, and it
    is asked in the live test below.
    """

    @pytest.mark.parametrize(
        "said",
        [
            "yeah, interesting",
            "go on",
            "thanks",
        ],
    )
    def test_small_talk_never_reaches_a_model(
        self, make_formulator, make_turn, chat_session, said
    ):
        llm = FakeLLMProvider([])
        formulator = make_formulator(StubGraph(), llm=llm)

        assert not formulator.formulate(make_turn(said), chat_session).should_retrieve
        assert llm.calls == []

    def test_a_childhood_mention_becomes_a_grounded_era_lookup(
        self, make_formulator, make_turn, make_reply, chat_session
    ):
        formulator = make_formulator(
            StubGraph(eras=("CHILDHOOD_HOME",)),
            script=[
                make_reply(
                    triggers=[
                        {"trigger_type": "HISTORICAL_ERA", "era": "childhood home"}
                    ],
                    register="VULNERABLE",
                )
            ],
        )

        signal = formulator.formulate(
            make_turn("I think since childhood I haven't gone out alone"), chat_session
        )

        assert signal.retrieval_triggers[0].era == "CHILDHOOD_HOME"
        assert signal.emotional_register is EmotionalRegister.VULNERABLE

    def test_a_physical_feeling_becomes_a_somatic_lookup(
        self, make_formulator, make_turn, make_reply, chat_session
    ):
        formulator = make_formulator(
            StubGraph(),
            script=[make_reply(triggers=[{"trigger_type": "SOMATIC_MARKER"}])],
        )

        signal = formulator.formulate(
            make_turn("I can feel that resistance in my chest again"), chat_session
        )

        assert signal.trigger_types == (TriggerType.SOMATIC_MARKER,)


class TestOrderingReasons:
    def test_a_kind_with_no_place_in_the_order_sorts_last(self):
        # NO_TRIGGER never reaches this, but a stray value from a model
        # should sort somewhere harmless rather than fail the whole turn.
        from lumen.query.formulation.contracts import TRIGGER_PRECEDENCE, precedence_of

        assert precedence_of(TriggerType.NO_TRIGGER) == len(TRIGGER_PRECEDENCE)

    def test_every_real_kind_has_a_place(self):
        from lumen.query.formulation.contracts import TRIGGER_PRECEDENCE, precedence_of

        real = [kind for kind in TriggerType if kind is not TriggerType.NO_TRIGGER]

        assert sorted(precedence_of(kind) for kind in real) == list(
            range(len(TRIGGER_PRECEDENCE))
        )
