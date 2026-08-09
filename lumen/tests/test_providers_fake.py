"""
Tests for the scripted stand-in providers.

Two things matter most here. The fake must go through the same code path as a
real provider, or tests using it are checking something other than what runs in
production. And it must fail loudly when it runs out of script, because a
stand-in that invents an answer lets a test pass after the prompt it was
checking has changed.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from lumen.providers.errors import FakeScriptExhaustedError
from lumen.providers.fake import (
    FakeEmbeddingProvider,
    FakeLLMProvider,
    FakeScriptRegistry,
)
from lumen.providers.protocols import EmbeddingProvider, LLMProvider
from lumen.providers.results import ChatMessage
from lumen.schemas.enums import EmbeddingTaskType, ModelRole


class Answer(BaseModel):
    answer: str


class TestScriptAsAList:
    def test_replies_are_handed_out_in_order(self):
        provider = FakeLLMProvider(['{"answer": "one"}', '{"answer": "two"}'])
        assert provider.generate_structured("q", Answer).data == {"answer": "one"}
        assert provider.generate_structured("q", Answer).data == {"answer": "two"}

    def test_running_out_of_script_is_an_error(self):
        provider = FakeLLMProvider(['{"answer": "only one"}'])
        provider.generate_structured("q", Answer)
        with pytest.raises(FakeScriptExhaustedError):
            provider.generate_structured("q", Answer)

    def test_the_error_says_how_far_it_got(self):
        provider = FakeLLMProvider(['{"answer": "one"}'])
        provider.generate_structured("q", Answer)
        with pytest.raises(FakeScriptExhaustedError, match="call number 2"):
            provider.generate_structured("q", Answer)

    def test_no_script_at_all_fails_immediately(self):
        with pytest.raises(FakeScriptExhaustedError):
            FakeLLMProvider().generate_structured("q", Answer)


class TestScriptAsALookup:
    def test_the_matching_entry_is_used(self):
        provider = FakeLLMProvider({"weather": '{"answer": "sunny"}'})
        assert provider.generate_structured("what is the weather", Answer).data == {
            "answer": "sunny"
        }

    def test_a_lookup_can_be_used_repeatedly(self):
        """Unlike a list, it does not run out."""
        provider = FakeLLMProvider({"weather": '{"answer": "sunny"}'})
        provider.generate_structured("the weather today", Answer)
        assert provider.generate_structured("the weather tomorrow", Answer).data == {
            "answer": "sunny"
        }

    def test_a_prompt_that_matches_nothing_is_an_error(self):
        provider = FakeLLMProvider({"weather": '{"answer": "sunny"}'})
        with pytest.raises(FakeScriptExhaustedError, match="no scripted reply"):
            provider.generate_structured("something else", Answer)

    def test_the_error_lists_what_was_available(self):
        provider = FakeLLMProvider({"weather": "{}", "traffic": "{}"})
        with pytest.raises(FakeScriptExhaustedError, match="traffic"):
            provider.generate_structured("something else", Answer)


class TestScriptAsAFunction:
    def test_the_function_receives_the_prompt(self):
        seen = []

        def script(prompt: str) -> str:
            seen.append(prompt)
            return '{"answer": "ok"}'

        FakeLLMProvider(script).generate_structured("the prompt", Answer)
        assert seen == ["the prompt"]

    def test_the_reply_can_depend_on_the_prompt(self):
        provider = FakeLLMProvider(lambda prompt: '{"answer": "%s"}' % prompt.upper())
        assert provider.generate_structured("hi", Answer).data == {"answer": "HI"}


class TestRecordingCalls:
    def test_what_was_asked_is_remembered(self):
        provider = FakeLLMProvider(['{"answer": "ok"}'])
        provider.generate_structured("the question", Answer, system_instruction="be brief")

        call = provider.calls[0]
        assert call.operation == "generate_structured"
        assert call.prompt == "the question"
        assert call.response_model == "Answer"
        assert call.system_instruction == "be brief"

    def test_the_temperature_used_is_remembered(self):
        provider = FakeLLMProvider(['{"answer": "ok"}'])
        provider.generate_structured("q", Answer, temperature=0.7)
        assert provider.calls[0].temperature == 0.7

    def test_conversations_are_recorded_too(self):
        provider = FakeLLMProvider(["hello"])
        provider.generate_text([ChatMessage(role="user", content="hi")])
        assert provider.calls[0].operation == "generate_text"
        assert "hi" in provider.calls[0].prompt


class TestBehavingLikeARealProvider:
    def test_it_satisfies_the_interface(self):
        assert isinstance(FakeLLMProvider([]), LLMProvider)

    def test_it_reports_a_provider_and_model(self):
        provider = FakeLLMProvider(['{"answer": "ok"}'])
        result = provider.generate_structured("q", Answer)
        assert (result.provider, result.model) == ("fake", "fake-model")

    def test_unreadable_json_is_reported_not_raised(self):
        """Same as a real provider, so tests exercise the real failure path."""
        provider = FakeLLMProvider(["this is not json"])
        result = provider.generate_structured("q", Answer)
        assert result.data is None
        assert result.parse_error is not None

    def test_timings_are_reported(self):
        provider = FakeLLMProvider(['{"answer": "ok"}'])
        result = provider.generate_structured("q", Answer)
        assert result.latency_ms >= 0
        assert result.elapsed_ms >= 0

    def test_token_counts_are_reported(self):
        provider = FakeLLMProvider(["one two three"])
        result = provider.generate_text([ChatMessage(role="user", content="hi")])
        assert result.usage.completion_tokens == 3

    def test_the_role_can_be_chosen(self):
        provider = FakeLLMProvider(["ok"], role=ModelRole.THINKING)
        assert provider.model_role is ModelRole.THINKING

    def test_closing_is_noticed(self):
        provider = FakeLLMProvider([])
        provider.close()
        assert provider.closed is True


class TestFakeEmbedding:
    def test_it_satisfies_the_interface(self):
        assert isinstance(FakeEmbeddingProvider(), EmbeddingProvider)

    def test_the_vector_is_the_expected_width(self):
        assert len(FakeEmbeddingProvider(dimensions=64).embed_text("hello")) == 64

    def test_the_same_text_always_gives_the_same_vector(self):
        provider = FakeEmbeddingProvider(dimensions=32)
        assert provider.embed_text("hello") == provider.embed_text("hello")

    def test_different_text_gives_a_different_vector(self):
        provider = FakeEmbeddingProvider(dimensions=32)
        assert provider.embed_text("hello") != provider.embed_text("goodbye")

    def test_two_providers_agree(self):
        """So a result does not depend on which object happened to be used."""
        first = FakeEmbeddingProvider(dimensions=32)
        second = FakeEmbeddingProvider(dimensions=32)
        assert first.embed_text("hello") == second.embed_text("hello")

    def test_the_vector_has_a_length_of_one(self):
        """Real embedding models produce these, and cosine similarity assumes it."""
        vector = FakeEmbeddingProvider(dimensions=128).embed_text("hello")
        assert sum(value * value for value in vector) == pytest.approx(1.0)

    def test_a_batch_keeps_its_order(self):
        provider = FakeEmbeddingProvider(dimensions=16)
        vectors = provider.embed_batch(["a", "b", "c"])
        assert vectors[0] == provider.embed_text("a")
        assert vectors[2] == provider.embed_text("c")

    def test_an_empty_batch_gives_nothing(self):
        assert FakeEmbeddingProvider().embed_batch([]) == []

    def test_what_was_embedded_is_remembered(self):
        provider = FakeEmbeddingProvider(dimensions=16)
        provider.embed_batch(["a", "b"])
        assert provider.embedded == ["a", "b"]

    def test_the_task_makes_no_difference(self):
        """It has no model to hand the task to, so it ignores it consistently."""
        provider = FakeEmbeddingProvider(dimensions=16)
        assert provider.embed_text("x", task_type=EmbeddingTaskType.QUERY) == provider.embed_text(
            "x", task_type=EmbeddingTaskType.DOCUMENT
        )

    def test_closing_is_noticed(self):
        provider = FakeEmbeddingProvider()
        provider.close()
        assert provider.closed is True


class TestScriptRegistry:
    def test_a_script_can_be_left_and_collected(self):
        registry = FakeScriptRegistry()
        registry.register(ModelRole.LIGHTWEIGHT, ["reply"])
        assert registry.get(ModelRole.LIGHTWEIGHT) == ["reply"]

    def test_an_unset_role_has_nothing(self):
        assert FakeScriptRegistry().get(ModelRole.THINKING) is None

    def test_roles_are_kept_apart(self):
        registry = FakeScriptRegistry()
        registry.register(ModelRole.LIGHTWEIGHT, ["fast"])
        registry.register(ModelRole.THINKING, ["deep"])
        assert registry.get(ModelRole.LIGHTWEIGHT) == ["fast"]
        assert registry.get(ModelRole.THINKING) == ["deep"]

    def test_registering_again_replaces(self):
        registry = FakeScriptRegistry()
        registry.register(ModelRole.LIGHTWEIGHT, ["first"])
        registry.register(ModelRole.LIGHTWEIGHT, ["second"])
        assert registry.get(ModelRole.LIGHTWEIGHT) == ["second"]

    def test_clearing_removes_everything(self):
        """So one test cannot affect the next."""
        registry = FakeScriptRegistry()
        registry.register(ModelRole.LIGHTWEIGHT, ["reply"])
        registry.clear()
        assert registry.get(ModelRole.LIGHTWEIGHT) is None
