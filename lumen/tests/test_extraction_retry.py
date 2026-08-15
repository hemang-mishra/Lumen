"""
Tests for asking again about the parts of a reading that came back
unusable.

Three properties are tested repeatedly here.

Recovery works: an item refused for something fixable comes back usable and
costs one extra call, not a whole second reading.

Nothing already accepted is disturbed. Only the refused items are asked
about, so a good finding from the first attempt appears in the result
unchanged no matter how many corrections follow it.

The loop stops. Four different things end it — everything accepted, nothing
left worth asking about, an attempt that changed nothing, and the attempt
cap — and each is tested by counting the calls actually made.
"""

from __future__ import annotations

import json
import logging

import pytest

from lumen.config import AppConfig, PipelineConfig
from lumen.pipeline import extract
from lumen.pipeline.extraction import retry
from lumen.providers.fake import FakeLLMProvider
from lumen.schemas.enums import ModelRole, ObservationStatus, ObservationType


def reply(*observations, events=(), chains=()) -> str:
    return json.dumps(
        {
            "observations": list(observations),
            "events": list(events),
            "causal_mechanisms": list(chains),
        }
    )


def good(content: str = "felt small", kind: str = "EMOTION") -> dict:
    return {"type": kind, "content": content, "raw_evidence": ["felt small"]}


def invented_type(content: str = "the comparing hurts") -> dict:
    return {"type": "VIBES", "content": content, "raw_evidence": ["felt small"]}


def attempts_capped(count: int) -> AppConfig:
    return AppConfig(pipeline=PipelineConfig(max_extraction_attempts=count))


def scripted(replies: list[str]) -> FakeLLMProvider:
    """A model that answers each call in turn, whatever it is asked."""
    return FakeLLMProvider(list(replies), role=ModelRole.THINKING, model="fake-thinker")


def idle_light() -> FakeLLMProvider:
    """A fast model that should never be called on a close reading."""
    return FakeLLMProvider([], role=ModelRole.LIGHTWEIGHT, model="fake-light")


class TestRecovery:
    def test_a_fixable_item_is_asked_about_and_recovered(self, make_extraction_input):
        model = scripted(
            [reply(good(), invented_type()), reply(good("the comparing hurts", "PATTERN"))]
        )

        result = extract(
            make_extraction_input(), lightweight=idle_light(), thinking=model
        )

        assert len(model.calls) == 2
        assert [node.type for node in result.observations] == [
            ObservationType.EMOTION,
            ObservationType.PATTERN,
        ]
        assert result.failed_observations == []

    def test_a_recovered_reading_is_trusted_again(self, make_extraction_input):
        # A stumble that was fixed cost nothing in the end. Reporting the
        # run as untrustworthy would make the flag useless for spotting
        # losses that are real.
        model = scripted(
            [reply(good(), invented_type()), reply(good("the comparing hurts", "PATTERN"))]
        )

        result = extract(
            make_extraction_input(), lightweight=idle_light(), thinking=model
        )

        assert result.validation_passed is True
        assert result.retry_count == 1

    def test_the_attempt_that_produced_each_finding_is_recorded(
        self, make_extraction_input
    ):
        model = scripted(
            [reply(good(), invented_type()), reply(good("the comparing hurts", "PATTERN"))]
        )

        result = extract(
            make_extraction_input(), lightweight=idle_light(), thinking=model
        )

        assert [node.extraction_attempt for node in result.observations] == [1, 2]

    def test_findings_recovered_late_get_names_of_their_own(
        self, make_extraction_input
    ):
        # Each attempt would otherwise start counting from one and hand out
        # names the previous attempt had already used.
        model = scripted(
            [reply(good(), invented_type()), reply(good("the comparing hurts", "PATTERN"))]
        )

        result = extract(
            make_extraction_input(), lightweight=idle_light(), thinking=model
        )

        names = [node.node_id for node in result.observations]
        assert len(set(names)) == len(names)

    def test_an_episode_saved_by_a_correction_still_gets_an_anchor(
        self, make_extraction_input
    ):
        # Nothing survived the first reading, so nothing was anchored. If
        # the anchor were not minted late, an episode rescued by its second
        # attempt would be the one episode a belief could never change in.
        model = scripted([reply(invented_type()), reply(good("recovered", "PATTERN"))])

        result = extract(
            make_extraction_input(), lightweight=idle_light(), thinking=model
        )

        assert len(result.observations) == 1
        assert len(result.sessions) == 1

    def test_a_second_correction_runs_while_progress_is_being_made(
        self, make_extraction_input
    ):
        model = scripted(
            [
                reply(invented_type("first"), invented_type("second")),
                reply(good("first", "PATTERN")),
                reply(good("second", "BELIEF")),
            ]
        )

        result = extract(
            make_extraction_input(), lightweight=idle_light(), thinking=model
        )

        assert len(model.calls) == 3
        assert len(result.observations) == 2
        assert result.retry_count == 2


class TestGivingUp:
    def test_an_unfixable_item_is_kept_as_a_failure(self, make_extraction_input):
        model = scripted([reply(good(), invented_type()), reply(invented_type())])

        result = extract(
            make_extraction_input(), lightweight=idle_light(), thinking=model
        )

        assert len(result.failed_observations) == 1
        assert result.failed_observations[0].status is ObservationStatus.EXTRACTION_FAILED

    def test_a_failure_never_appears_among_the_real_findings(
        self, make_extraction_input
    ):
        model = scripted([reply(good(), invented_type()), reply(invented_type())])

        result = extract(
            make_extraction_input(), lightweight=idle_light(), thinking=model
        )

        assert [node.type for node in result.observations] == [ObservationType.EMOTION]
        assert all(
            node.status is ObservationStatus.ACTIVE for node in result.observations
        )

    def test_giving_up_means_the_reading_is_not_trusted(self, make_extraction_input):
        model = scripted([reply(invented_type()), reply(invented_type())])

        result = extract(
            make_extraction_input(), lightweight=idle_light(), thinking=model
        )

        assert result.validation_passed is False

    def test_the_person_can_still_see_what_could_not_be_read(
        self, make_extraction_input
    ):
        model = scripted([reply(invented_type("the comparing hurts")), reply()])

        result = extract(
            make_extraction_input(), lightweight=idle_light(), thinking=model
        )

        failed = result.failed_observations[0]
        assert failed.content == "the comparing hurts"
        assert any("VIBES" in note for note in failed.raw_evidence)
        assert any("UNKNOWN_TYPE" in note for note in failed.raw_evidence)

    def test_an_item_the_model_declined_to_fix_is_still_kept(
        self, make_extraction_input
    ):
        # Leaving it out is an answer the correction explicitly allows, and
        # a more honest one than a forced guess — but the person should
        # still be shown what was lost.
        model = scripted([reply(invented_type()), reply()])

        result = extract(
            make_extraction_input(), lightweight=idle_light(), thinking=model
        )

        assert len(result.failed_observations) == 1
        assert result.failed_observations[0].extraction_attempt == 2


class TestNothingUnfixableIsAskedAbout:
    def test_a_category_needing_audio_costs_no_extra_call(
        self, make_extraction_input
    ):
        model = scripted(
            [
                reply(
                    good(),
                    {
                        "type": "PROSODY_SIGNAL",
                        "content": "voice tightened",
                        "extraction_signal_strength": "HIGH",
                        "raw_evidence": ["felt small"],
                    },
                )
            ]
        )

        result = extract(
            make_extraction_input(), lightweight=idle_light(), thinking=model
        )

        assert len(model.calls) == 1
        assert result.retry_count == 0

    def test_a_category_needing_audio_leaves_no_failure_record(
        self, make_extraction_input
    ):
        # A failure record exists to ask a person for help, and there is
        # nothing a person can do about a recording that was never made.
        model = scripted(
            [
                reply(
                    good(),
                    {
                        "type": "PROSODY_SIGNAL",
                        "content": "voice tightened",
                        "extraction_signal_strength": "HIGH",
                        "raw_evidence": ["felt small"],
                    },
                )
            ]
        )

        result = extract(
            make_extraction_input(), lightweight=idle_light(), thinking=model
        )

        assert result.failed_observations == []

    def test_a_one_step_sequence_costs_no_extra_call(self, make_extraction_input):
        model = scripted(
            [
                reply(
                    good(),
                    chains=[
                        {
                            "chain_summary": "not really a sequence",
                            "causal_chain": [
                                {"step": 1, "type": "TRIGGER", "content": "saw the post"}
                            ],
                        }
                    ],
                )
            ]
        )

        result = extract(
            make_extraction_input(), lightweight=idle_light(), thinking=model
        )

        assert len(model.calls) == 1

    def test_a_mixed_reply_asks_only_about_the_fixable_half(
        self, make_extraction_input
    ):
        model = scripted(
            [
                reply(
                    invented_type(),
                    {
                        "type": "PROSODY_SIGNAL",
                        "content": "voice tightened",
                        "extraction_signal_strength": "HIGH",
                    },
                ),
                reply(good("the comparing hurts", "PATTERN")),
            ]
        )

        result = extract(
            make_extraction_input(), lightweight=idle_light(), thinking=model
        )

        assert len(model.calls) == 2
        assert len(result.observations) == 1
        assert result.failed_observations == []


class TestTheLoopStops:
    def test_an_attempt_that_changes_nothing_ends_it(self, make_extraction_input):
        # A model that returned the same unusable answer once will return it
        # again. Spending a third call to watch that happen helps nobody.
        model = scripted([reply(invented_type()), reply(invented_type()), reply(good())])

        result = extract(
            make_extraction_input(), lightweight=idle_light(), thinking=model
        )

        assert len(model.calls) == 2
        assert len(result.failed_observations) == 1

    def test_a_clean_reading_never_asks_again(self, make_extraction_input):
        model = scripted([reply(good())])

        result = extract(
            make_extraction_input(), lightweight=idle_light(), thinking=model
        )

        assert len(model.calls) == 1
        assert result.retry_count == 0

    def test_the_cap_is_honoured(self, make_extraction_input):
        model = scripted(
            [
                reply(invented_type("a"), invented_type("b")),
                reply(good("a", "PATTERN")),
                reply(good("b", "BELIEF")),
                reply(good("c", "LESSON")),
            ]
        )

        result = extract(
            make_extraction_input(),
            lightweight=idle_light(),
            thinking=model,
            config=attempts_capped(2),
        )

        assert len(model.calls) == 2
        assert result.retry_count == 1

    def test_one_attempt_allowed_means_no_corrections_at_all(
        self, make_extraction_input
    ):
        # This is Goal 6's behaviour, still reachable by configuration.
        model = scripted([reply(good(), invented_type())])

        result = extract(
            make_extraction_input(),
            lightweight=idle_light(),
            thinking=model,
            config=attempts_capped(1),
        )

        assert len(model.calls) == 1
        assert result.retry_count == 0
        assert len(result.failed_observations) == 1

    def test_a_nonsense_cap_is_treated_as_one_attempt(self, make_extraction_input):
        model = scripted([reply(good())])

        extract(
            make_extraction_input(),
            lightweight=idle_light(),
            thinking=model,
            config=attempts_capped(0),
        )

        assert len(model.calls) == 1


class TestWhenTheReadingItselfFails:
    def test_an_unreadable_reply_is_asked_for_again(self, make_extraction_input):
        model = scripted(["not json at all", reply(good())])

        result = extract(
            make_extraction_input(), lightweight=idle_light(), thinking=model
        )

        assert len(model.calls) == 2
        assert len(result.observations) == 1
        assert result.read_failed is False

    def test_giving_up_on_the_reading_says_so(self, make_extraction_input):
        model = scripted(["not json", "still not json", "nor this"])

        result = extract(
            make_extraction_input(), lightweight=idle_light(), thinking=model
        )

        assert len(model.calls) == 3
        assert result.read_failed is True
        assert result.retry_count == 2

    def test_nothing_is_invented_to_cover_a_failed_reading(
        self, make_extraction_input
    ):
        model = scripted(["not json", "still not json", "nor this"])

        result = extract(
            make_extraction_input(), lightweight=idle_light(), thinking=model
        )

        assert result.observations == []
        assert result.sessions == []
        assert result.failed_observations == []
        assert result.validation_passed is False

    def test_a_correction_that_never_arrives_ends_the_loop(
        self, make_extraction_input
    ):
        model = scripted([reply(invented_type()), "not json", reply(good())])

        result = extract(
            make_extraction_input(), lightweight=idle_light(), thinking=model
        )

        assert len(model.calls) == 2
        assert len(result.failed_observations) == 1
        assert result.read_failed is False


class TestTheThinPathIsNeverCorrected:
    def test_a_feeling_with_no_quote_is_never_asked_about_again(
        self, make_extraction_input, extraction_providers
    ):
        # The most important test in this file. Asking again here would be
        # an instruction to produce the missing quote, and the produced one
        # would pass the check it was meant to fail.
        from lumen.schemas.enums import EntryClass

        light, thinking = extraction_providers(
            {
                "raw_capture": json.dumps(
                    {
                        "context": "Mentions a cafe",
                        "emotion": "exhausted",
                        "emotion_quote": "I am completely exhausted",
                    }
                )
            }
        )

        result = extract(
            make_extraction_input(entry_class=EntryClass.RAW_CAPTURE),
            lightweight=light,
            thinking=thinking,
        )

        assert len(light.calls) == 1
        assert thinking.calls == []
        assert [node.type for node in result.observations] == [ObservationType.CONTEXT]
        assert result.failed_observations == []


class TestWhatGetsLogged:
    def test_each_correction_reports_what_it_asked_about(
        self, make_extraction_input, captured_logs
    ):
        model = scripted(
            [reply(good(), invented_type()), reply(good("the comparing hurts", "PATTERN"))]
        )

        extract(make_extraction_input(), lightweight=idle_light(), thinking=model)

        line = next(
            entry
            for entry in captured_logs
            if entry["msg"] == "extraction correction attempted"
        )
        assert line["attempt"] == 2
        assert line["asked_about"] == 1
        assert line["rules"] == ["UNKNOWN_TYPE"]
        assert line["recovered"] == 1

    def test_giving_up_is_warned_about(self, make_extraction_input, caplog):
        model = scripted([reply(invented_type()), reply(invented_type())])

        with caplog.at_level(logging.WARNING):
            extract(make_extraction_input(), lightweight=idle_light(), thinking=model)

        warnings = [r for r in caplog.records if hasattr(r, "abandoned")]
        assert len(warnings) == 1
        assert warnings[0].recorded_as_failed == 1

    def test_a_re_reading_is_recorded(self, make_extraction_input, captured_logs):
        model = scripted(["not json", reply(good())])

        extract(make_extraction_input(), lightweight=idle_light(), thinking=model)

        assert any(
            entry["msg"] == "extraction re-reading after an unusable reply"
            for entry in captured_logs
        )

    def test_the_correction_never_puts_the_writing_in_the_log(
        self, make_extraction_input, captured_logs
    ):
        private = "the thing about my father I never said out loud"
        model = scripted(
            [reply({"type": "VIBES", "content": private, "raw_evidence": [private]}), reply()]
        )

        extract(
            make_extraction_input(f"I wrote down {private} and felt lighter."),
            lightweight=idle_light(),
            thinking=model,
        )

        assert private not in json.dumps(captured_logs)


class TestOnlyFindingsCanFail:
    def test_a_refused_event_leaves_no_failure_record(self, make_extraction_input):
        # There is no place in the graph for a failed event, so it is noted
        # in the log and goes no further. A known gap, not an oversight.
        model = scripted(
            [
                reply(good(), events=[{"event_summary": "went out", "signal_strength": "HUGE"}]),
                reply(),
            ]
        )

        result = extract(
            make_extraction_input(), lightweight=idle_light(), thinking=model
        )

        assert result.failed_observations == []
        assert result.events == []

    def test_a_refused_sequence_leaves_no_failure_record(self, make_extraction_input):
        model = scripted(
            [
                reply(
                    good(),
                    chains=[
                        {
                            "chain_summary": "a to b",
                            "causal_chain": [
                                {"step": 1, "type": "MADE_UP", "content": "x"},
                                {"step": 2, "type": "OUTCOME", "content": "y"},
                            ],
                        }
                    ],
                ),
                reply(),
            ]
        )

        result = extract(
            make_extraction_input(), lightweight=idle_light(), thinking=model
        )

        assert result.failed_observations == []
        assert result.causal_chains == []


class TestTheRetryableTableIsUsed:
    @pytest.mark.parametrize("rule", sorted(retry.RETRYABLE_RULES))
    def test_every_retryable_rule_is_one_the_checker_can_raise(self, rule):
        # Guards against a rule sitting in the table that nothing produces,
        # which would look like coverage and provide none.
        from lumen.pipeline.extraction.contracts import DropRule

        assert rule in set(DropRule)
