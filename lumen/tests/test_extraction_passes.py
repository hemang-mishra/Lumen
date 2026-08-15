"""
Tests for the two ways of reading an episode.

Most of these are about what happens when the reading fails. A model call
can fail for reasons nobody controls, and the choice made here is that a
failure produces nothing at all rather than something partial or invented.
That is only safe because the person's writing is already stored on the
episode itself, so the reading can be run again — and only useful if it is
loud, so every failure is also asserted to leave a warning behind.
"""

from __future__ import annotations

import json
import logging

import pytest

from lumen.config import PipelineConfig
from lumen.pipeline.extraction import passes
from lumen.providers.errors import ProviderTimeoutError
from lumen.providers.fake import FakeLLMProvider
from lumen.schemas.enums import EntryClass, ModelRole, ObservationType, Provenance

LIMITS = PipelineConfig()


def reflection_reply(**overrides) -> str:
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


def provider(reply: str, *, role: ModelRole = ModelRole.THINKING) -> FakeLLMProvider:
    """A model that answers anything with one scripted reply."""
    return FakeLLMProvider([reply], role=role, model="fake-thinker")


class FailingProvider(FakeLLMProvider):
    """A model whose calls never get through."""

    def _request_structured(self, **kwargs):
        raise ProviderTimeoutError(
            "took too long", provider="fake", model="fake-thinker", role=self.model_role
        )


class TestReadingClosely:
    def test_a_good_reply_becomes_nodes(self, make_extraction_input):
        outcome = passes.read_reflection(
            make_extraction_input(), provider=provider(reflection_reply()), limits=LIMITS
        )

        assert len(outcome.observations) == 1
        assert outcome.observations[0].type is ObservationType.EMOTION
        assert outcome.used_fallback is False

    def test_the_prompt_carries_the_vocabulary_and_the_people(
        self, make_extraction_input
    ):
        model = provider(reflection_reply())

        passes.read_reflection(
            make_extraction_input(people=["Alex"]), provider=model, limits=LIMITS
        )

        prompt = model.calls[0].prompt
        assert "METACOGNITIVE_INTERRUPT" in prompt
        assert "Alex" in prompt

    def test_the_prompt_never_offers_a_category_needing_audio(
        self, make_extraction_input
    ):
        model = provider(reflection_reply())

        passes.read_reflection(make_extraction_input(), provider=model, limits=LIMITS)

        assert "PROSODY_SIGNAL" not in model.calls[0].prompt

    def test_an_unsettled_reference_is_flagged_as_unsettled(self, make_extraction_input):
        model = provider(reflection_reply())

        passes.read_reflection(
            make_extraction_input(ambiguous=[("this guy", ["Alex", "Rohan"])]),
            provider=model,
            limits=LIMITS,
        )

        assert "Do not choose one" in model.calls[0].prompt

    def test_the_shape_asked_for_is_the_reflection_shape(self, make_extraction_input):
        model = provider(reflection_reply())

        passes.read_reflection(make_extraction_input(), provider=model, limits=LIMITS)

        assert model.calls[0].response_model == "ReflectionExtractionResponse"

    def test_an_anchor_is_minted_when_something_was_found(self, make_extraction_input):
        outcome = passes.read_reflection(
            make_extraction_input(), provider=provider(reflection_reply()), limits=LIMITS
        )

        assert len(outcome.sessions) == 1

    def test_an_episode_that_yielded_nothing_gets_no_anchor(self, make_extraction_input):
        # A session node standing alone would claim a piece of thinking
        # happened that left no trace of itself.
        outcome = passes.read_reflection(
            make_extraction_input(),
            provider=provider(reflection_reply(observations=[])),
            limits=LIMITS,
        )

        assert outcome.sessions == ()
        assert outcome.is_empty is True

    def test_only_one_anchor_is_minted_however_much_was_found(
        self, make_extraction_input
    ):
        reply = reflection_reply(
            observations=[
                {"type": "EMOTION", "content": "felt small", "raw_evidence": ["felt small"]},
                {"type": "LESSON", "content": "comparing hurts", "raw_evidence": ["hurts"]},
            ],
            events=[
                {"event_summary": "Ate at the cafe", "raw_evidence": ["cafe"]},
                {"event_summary": "Read a post", "raw_evidence": ["shipped"]},
            ],
        )

        outcome = passes.read_reflection(
            make_extraction_input(), provider=provider(reply), limits=LIMITS
        )

        assert len(outcome.sessions) == 1


class TestReadingThinly:
    def test_the_topic_and_a_stated_feeling_become_nodes(self, make_extraction_input):
        reply = json.dumps(
            {
                "context": "Mentions a cafe and comparing himself to Alex",
                "emotion": "small",
                "emotion_quote": "felt small",
            }
        )

        outcome = passes.read_raw_capture(
            make_extraction_input(entry_class=EntryClass.RAW_CAPTURE),
            provider=provider(reply, role=ModelRole.LIGHTWEIGHT),
            limits=LIMITS,
        )

        assert [item.type for item in outcome.observations] == [
            ObservationType.CONTEXT,
            ObservationType.EMOTION,
        ]

    def test_the_thin_path_never_mints_an_anchor(self, make_extraction_input):
        reply = json.dumps({"context": "Mentions a cafe"})

        outcome = passes.read_raw_capture(
            make_extraction_input(entry_class=EntryClass.RAW_CAPTURE),
            provider=provider(reply, role=ModelRole.LIGHTWEIGHT),
            limits=LIMITS,
        )

        assert outcome.sessions == ()

    def test_the_thin_prompt_does_not_carry_the_vocabulary(self, make_extraction_input):
        model = provider(json.dumps({"context": "a cafe"}), role=ModelRole.LIGHTWEIGHT)

        passes.read_raw_capture(
            make_extraction_input(entry_class=EntryClass.RAW_CAPTURE),
            provider=model,
            limits=LIMITS,
        )

        prompt = model.calls[0].prompt
        assert "METACOGNITIVE_INTERRUPT" not in prompt
        assert model.calls[0].response_model == "RawCaptureResponse"

    def test_findings_from_a_thin_entry_are_the_persons_own(self, make_extraction_input):
        reply = json.dumps({"context": "Mentions a cafe"})

        outcome = passes.read_raw_capture(
            make_extraction_input(entry_class=EntryClass.RAW_CAPTURE),
            provider=provider(reply, role=ModelRole.LIGHTWEIGHT),
            limits=LIMITS,
        )

        assert outcome.observations[0].provenance is Provenance.USER_GENERATED


def extraction_warnings(caplog) -> list[logging.LogRecord]:
    """
    The warnings this stage left, ignoring the model layer's own.

    The provider logs its own warnings about retries and unreadable replies.
    Counting those as well would let a stage that had gone silent still look
    like it was reporting failures.
    """
    return [record for record in caplog.records if hasattr(record, "extraction_pass")]


class TestWhenTheReadingFails:
    # The three ways a reading can fail, with the reason each should be
    # reported as. The unusable reply differs by path because each path asks
    # for a different shape.
    BREAKAGES = {
        "call_failed": ("provider_error", None, None),
        "not_json": ("unparseable_response", "not json at all", "not json at all"),
        "wrong_shape": (
            "unexpected_shape",
            json.dumps({"observations": "not a list"}),
            json.dumps({"context": ["not", "a", "sentence"]}),
        ),
    }

    @pytest.fixture(params=sorted(BREAKAGES))
    def breakage(self, request):
        return request.param

    def broken_model(self, kind: str, *, thin: bool) -> FakeLLMProvider:
        _, close_reply, thin_reply = self.BREAKAGES[kind]
        if kind == "call_failed":
            return FailingProvider([], role=ModelRole.THINKING)
        return provider(thin_reply if thin else close_reply)

    def test_nothing_is_extracted(self, make_extraction_input, breakage):
        outcome = passes.read_reflection(
            make_extraction_input(),
            provider=self.broken_model(breakage, thin=False),
            limits=LIMITS,
        )

        assert outcome.is_empty is True
        assert outcome.sessions == ()
        assert outcome.used_fallback is True

    def test_the_failure_is_warned_about_and_named(
        self, make_extraction_input, breakage, caplog
    ):
        expected_reason, _, _ = self.BREAKAGES[breakage]

        with caplog.at_level(logging.WARNING):
            passes.read_reflection(
                make_extraction_input(),
                provider=self.broken_model(breakage, thin=False),
                limits=LIMITS,
            )

        warnings = extraction_warnings(caplog)
        assert len(warnings) == 1
        assert warnings[0].extraction_pass == "reflection"
        assert warnings[0].reason == expected_reason

    def test_the_thin_path_fails_the_same_way(
        self, make_extraction_input, breakage, caplog
    ):
        with caplog.at_level(logging.WARNING):
            outcome = passes.read_raw_capture(
                make_extraction_input(entry_class=EntryClass.RAW_CAPTURE),
                provider=self.broken_model(breakage, thin=True),
                limits=LIMITS,
            )

        assert outcome.is_empty is True
        assert [r.extraction_pass for r in extraction_warnings(caplog)] == ["raw_capture"]

    def test_the_warning_never_repeats_the_writing(self, make_extraction_input, caplog):
        payload = make_extraction_input("The cafe smelled of burnt sugar and regret.")

        with caplog.at_level(logging.WARNING):
            passes.read_reflection(
                payload, provider=provider("not json"), limits=LIMITS
            )

        assert all("burnt sugar" not in record.getMessage() for record in caplog.records)


class TestPartialReplies:
    def test_missing_sections_are_treated_as_empty(self, make_extraction_input):
        # Every field has a default, so a reply that leaves out events costs
        # the events and nothing else.
        outcome = passes.read_reflection(
            make_extraction_input(),
            provider=provider(json.dumps({"observations": []})),
            limits=LIMITS,
        )

        assert outcome.used_fallback is False
        assert outcome.events == ()

    def test_extra_fields_in_a_reply_are_ignored(self, make_extraction_input):
        outcome = passes.read_reflection(
            make_extraction_input(),
            provider=provider(reflection_reply(mood_of_the_day="wistful")),
            limits=LIMITS,
        )

        assert len(outcome.observations) == 1

    def test_a_reply_with_one_bad_finding_keeps_the_rest(self, make_extraction_input):
        reply = reflection_reply(
            observations=[
                {"type": "NOT_A_REAL_TYPE", "content": "x", "raw_evidence": ["felt small"]},
                {"type": "EMOTION", "content": "felt small", "raw_evidence": ["felt small"]},
            ]
        )

        outcome = passes.read_reflection(
            make_extraction_input(), provider=provider(reply), limits=LIMITS
        )

        assert len(outcome.observations) == 1
        assert len(outcome.drops) == 1
