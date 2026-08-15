"""
End-to-end tests for the extraction stage.

These run the whole stage against scripted stand-in models, so they check
what actually ships: which model reads which kind of episode, what comes
out the other end, and whether the result honestly reports how much of it
can be relied on.

The worked example from the specification — the six-step sequence running
from a headache to a lesson about pacing — is here because it is the case
the design of causal chains was argued from, and a change that breaks it
is a change to what the system promises.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lumen.config import AppConfig, PipelineConfig
from lumen.pipeline import extract
from lumen.schemas.enums import (
    CausalStepType,
    EntryClass,
    ObservationStatus,
    ObservationType,
    SignalStrength,
)

HEADACHE_TEXT = (
    "I woke up with a headache on a normal workday and immediately felt "
    "pressure and confusion, like the day was already lost. So I relieved "
    "myself of every expectation and went at a very slow pace. Slowly I got "
    "absorbed in the work again, and after three hours I had more energy "
    "than I usually do. Going slowly is a real alternative to sleeping it off."
)


def reflection_reply(**overrides) -> str:
    """A reply from a close reading, with defaults a test can override."""
    payload = {
        "observations": [
            {
                "type": "EMOTION",
                "content": "Felt small after seeing what Alex shipped",
                "raw_evidence": ["felt small"],
            }
        ],
        "events": [],
        "causal_mechanisms": [],
    }
    payload.update(overrides)
    return json.dumps(payload)


def raw_capture_reply(**overrides) -> str:
    payload = {"context": "Mentions a cafe and comparing himself to Alex"}
    payload.update(overrides)
    return json.dumps(payload)


def headache_reply() -> str:
    """The worked six-step sequence from the specification."""
    return json.dumps(
        {
            "observations": [
                {
                    "type": "SOMATIC_STATE",
                    "content": "Woke with a headache",
                    "raw_evidence": ["I woke up with a headache"],
                },
                {
                    "type": "INTERVENTION_APPLIED",
                    "content": "Dropped all expectations and worked slowly",
                    "raw_evidence": ["went at a very slow pace"],
                },
                {
                    "type": "LESSON",
                    "content": "Slow progressive steps are a real alternative to sleeping it off",
                    "raw_evidence": ["Going slowly is a real alternative"],
                },
            ],
            "events": [],
            "causal_mechanisms": [
                {
                    "chain_summary": "Headache-triggered slowdown leading to restored energy",
                    "causal_chain": [
                        {"step": 1, "type": "TRIGGER", "content": "Headache on a normal workday"},
                        {"step": 2, "type": "INTERNAL_STATE", "content": "Pressure and confusion"},
                        {"step": 3, "type": "ACTION", "content": "Relieved all expectations, went slowly"},
                        {"step": 4, "type": "INTERNAL_STATE", "content": "Progressive re-engagement"},
                        {"step": 5, "type": "OUTCOME", "content": "Energy fully restored in three hours"},
                        {"step": 6, "type": "LESSON", "content": "Slow steps beat sleeping it off"},
                    ],
                }
            ],
        }
    )


class TestWhichModelReadsWhat:
    def test_a_close_reading_uses_the_reasoning_model_only(
        self, make_extraction_input, extraction_providers
    ):
        light, thinking = extraction_providers({"reflection": reflection_reply()})

        extract(make_extraction_input(), lightweight=light, thinking=thinking)

        assert len(thinking.calls) == 1
        assert light.calls == []

    def test_a_thin_entry_uses_the_fast_model_only(
        self, make_extraction_input, extraction_providers
    ):
        # Paying a reasoning model to summarise an entry the previous stage
        # already judged thin would undo the point of judging it.
        light, thinking = extraction_providers({"raw_capture": raw_capture_reply()})

        extract(
            make_extraction_input(entry_class=EntryClass.RAW_CAPTURE),
            lightweight=light,
            thinking=thinking,
        )

        assert len(light.calls) == 1
        assert thinking.calls == []

    def test_one_episode_costs_exactly_one_call(
        self, make_extraction_input, extraction_providers
    ):
        light, thinking = extraction_providers({"reflection": reflection_reply()})

        extract(make_extraction_input(), lightweight=light, thinking=thinking)

        assert len(light.calls) + len(thinking.calls) == 1

    def test_the_model_that_read_it_is_recorded(
        self, make_extraction_input, extraction_providers
    ):
        light, thinking = extraction_providers({"reflection": reflection_reply()})

        result = extract(make_extraction_input(), lightweight=light, thinking=thinking)

        assert result.extraction_model == "fake-thinker"
        assert all(node.extraction_model == "fake-thinker" for node in result.observations)


class TestTheWorkedExample:
    @pytest.fixture
    def headache(self, make_extraction_input, extraction_providers):
        light, thinking = extraction_providers({"reflection": headache_reply()})
        return extract(
            make_extraction_input(HEADACHE_TEXT), lightweight=light, thinking=thinking
        )

    def test_the_six_step_sequence_survives_intact(self, headache):
        assert len(headache.causal_chains) == 1
        assert len(headache.causal_steps) == 6
        assert headache.causal_chains[0].step_count == 6

    def test_the_steps_keep_their_order_and_kinds(self, headache):
        assert [step.step_type for step in headache.causal_steps] == [
            CausalStepType.TRIGGER,
            CausalStepType.INTERNAL_STATE,
            CausalStepType.ACTION,
            CausalStepType.INTERNAL_STATE,
            CausalStepType.OUTCOME,
            CausalStepType.LESSON,
        ]

    def test_a_rich_entry_yields_several_kinds_of_finding(self, headache):
        # The guard against quiet thinning: a prompt that drifts into
        # returning one bland finding per entry breaks nothing else.
        assert len({node.type for node in headache.observations}) >= 3

    def test_every_step_belongs_to_the_sequence(self, headache):
        chain_id = headache.causal_chains[0].node_id

        assert {step.chain_id for step in headache.causal_steps} == {chain_id}


class TestTheThinPath:
    def test_a_thin_entry_yields_at_most_a_topic_and_a_feeling(
        self, make_extraction_input, extraction_providers
    ):
        light, thinking = extraction_providers(
            {
                "raw_capture": raw_capture_reply(
                    emotion="small", emotion_quote="felt small"
                )
            }
        )

        result = extract(
            make_extraction_input(entry_class=EntryClass.RAW_CAPTURE),
            lightweight=light,
            thinking=thinking,
        )

        assert [node.type for node in result.observations] == [
            ObservationType.CONTEXT,
            ObservationType.EMOTION,
        ]

    def test_a_thin_entry_produces_no_sequences_and_no_anchor(
        self, make_extraction_input, extraction_providers
    ):
        light, thinking = extraction_providers({"raw_capture": raw_capture_reply()})

        result = extract(
            make_extraction_input(entry_class=EntryClass.RAW_CAPTURE),
            lightweight=light,
            thinking=thinking,
        )

        assert result.causal_chains == []
        assert result.causal_steps == []
        assert result.sessions == []

    def test_findings_from_a_thin_entry_are_marked_as_such(
        self, make_extraction_input, extraction_providers
    ):
        light, thinking = extraction_providers({"raw_capture": raw_capture_reply()})

        result = extract(
            make_extraction_input(entry_class=EntryClass.RAW_CAPTURE),
            lightweight=light,
            thinking=thinking,
        )

        assert result.observations[0].status is ObservationStatus.RAW_CAPTURE


class TestTheAnchor:
    def test_a_close_reading_always_leaves_something_to_anchor_against(
        self, make_extraction_input, extraction_providers
    ):
        # Nothing in the entry asks for this node. It exists so that a belief
        # can later be recorded as having changed, with a reason to point at.
        light, thinking = extraction_providers({"reflection": reflection_reply()})

        result = extract(make_extraction_input(), lightweight=light, thinking=thinking)

        assert len(result.sessions) == 1
        assert result.sessions[0].episode_id == result.episode_id

    def test_an_anchor_appears_even_when_events_were_found(
        self, make_extraction_input, extraction_providers
    ):
        light, thinking = extraction_providers(
            {
                "reflection": reflection_reply(
                    events=[{"event_summary": "Ate at the cafe", "raw_evidence": ["cafe"]}]
                )
            }
        )

        result = extract(make_extraction_input(), lightweight=light, thinking=thinking)

        assert len(result.sessions) == 1
        assert len(result.events) == 1

    def test_the_anchor_carries_the_weight_of_what_was_found(
        self, make_extraction_input, extraction_providers
    ):
        light, thinking = extraction_providers(
            {
                "reflection": reflection_reply(
                    observations=[
                        {
                            "type": "METACOGNITIVE_BREAKTHROUGH",
                            "content": "Realised the comparing is the problem",
                            "extraction_signal_strength": "CRITICAL",
                            "raw_evidence": ["The comparing is the thing that hurts"],
                        }
                    ]
                )
            }
        )

        result = extract(make_extraction_input(), lightweight=light, thinking=thinking)

        assert result.sessions[0].signal_strength is SignalStrength.CRITICAL


class TestHowFarTheResultCanBeTrusted:
    def test_a_clean_reading_is_trusted(
        self, make_extraction_input, extraction_providers
    ):
        light, thinking = extraction_providers({"reflection": reflection_reply()})

        result = extract(make_extraction_input(), lightweight=light, thinking=thinking)

        assert result.validation_passed is True
        assert result.retry_count == 0

    def test_a_reading_that_lost_something_is_not(
        self, make_extraction_input, extraction_providers
    ):
        light, thinking = extraction_providers(
            {
                "reflection": reflection_reply(
                    observations=[
                        {"type": "MADE_UP", "content": "x", "raw_evidence": ["felt small"]},
                        {"type": "EMOTION", "content": "felt small", "raw_evidence": ["felt small"]},
                    ]
                )
            }
        )

        result = extract(make_extraction_input(), lightweight=light, thinking=thinking)

        assert len(result.observations) == 1
        assert result.validation_passed is False

    def test_a_reading_that_found_nothing_is_not(
        self, make_extraction_input, extraction_providers
    ):
        light, thinking = extraction_providers(
            {"reflection": reflection_reply(observations=[])}
        )

        result = extract(make_extraction_input(), lightweight=light, thinking=thinking)

        assert result.validation_passed is False

    def test_a_reading_that_failed_outright_is_not(
        self, make_extraction_input, extraction_providers
    ):
        light, thinking = extraction_providers({"reflection": "not json"})

        result = extract(make_extraction_input(), lightweight=light, thinking=thinking)

        assert result.validation_passed is False
        assert result.observations == []


class TestNothingIsInventedToCoverAFailure:
    def test_a_failed_reading_yields_nothing_at_all(
        self, make_extraction_input, extraction_providers
    ):
        # The writing itself is already safely stored on the episode, so a
        # failure here costs a reading that can be run again. Filling the gap
        # would cost the truth of the history, undetectably.
        light, thinking = extraction_providers({"reflection": "not json"})

        result = extract(make_extraction_input(), lightweight=light, thinking=thinking)

        assert result.observations == []
        assert result.events == []
        assert result.sessions == []
        assert result.causal_chains == []
        assert result.causal_steps == []

    def test_the_episode_is_still_named_in_the_result(
        self, make_extraction_input, extraction_providers
    ):
        light, thinking = extraction_providers({"reflection": "not json"})
        payload = make_extraction_input()

        result = extract(payload, lightweight=light, thinking=thinking)

        assert result.episode_id == payload.episode.episode_id


class TestNamesAcrossEpisodes:
    def test_two_episodes_of_one_day_produce_separate_names(
        self, make_extraction_input, extraction_providers
    ):
        light, thinking = extraction_providers({"reflection": reflection_reply()})

        first = extract(
            make_extraction_input(episode_index=1, total=2),
            lightweight=light,
            thinking=thinking,
        )
        second = extract(
            make_extraction_input(episode_index=2, total=2),
            lightweight=light,
            thinking=thinking,
        )

        names = lambda result: {
            node.node_id for node in [*result.observations, *result.sessions]
        }
        assert not names(first) & names(second)


class TestLimitsAreHonoured:
    def test_a_runaway_reply_cannot_fill_the_graph(
        self, make_extraction_input, extraction_providers
    ):
        many = [
            {"type": "EMOTION", "content": f"feeling {n}", "raw_evidence": ["felt small"]}
            for n in range(40)
        ]
        light, thinking = extraction_providers(
            {"reflection": reflection_reply(observations=many)}
        )
        config = AppConfig(pipeline=PipelineConfig(max_observations_per_episode=5))

        result = extract(
            make_extraction_input(), lightweight=light, thinking=thinking, config=config
        )

        assert len(result.observations) == 5


class TestNoInfrastructure:
    def test_the_stage_reaches_for_no_database_or_store(self):
        # A stage that could read the graph would be a way to smuggle
        # history into a step whose whole value is not having any.
        package = Path(__file__).resolve().parents[1] / "pipeline"
        forbidden = ("lumen.operational", "lumen.graph", "lumen.vector")
        offenders = [
            (path.name, name)
            for path in package.rglob("*.py")
            for name in forbidden
            if name in path.read_text()
        ]

        assert offenders == []

    def test_the_stage_needs_nothing_installed(
        self, make_extraction_input, extraction_providers
    ):
        light, thinking = extraction_providers({"reflection": reflection_reply()})

        result = extract(make_extraction_input(), lightweight=light, thinking=thinking)

        assert result.observations
