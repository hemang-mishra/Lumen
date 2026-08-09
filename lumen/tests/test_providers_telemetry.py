"""
Tests for the log line each model call writes.

The one that matters most is that prompts stay out by default. Journal text in a
log file is a second copy of somebody's private history, sitting in plain text
next to the database that is supposed to hold it.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from lumen.config import ProviderConfig
from lumen.providers.errors import ProviderResponseError
from lumen.providers.fake import FakeEmbeddingProvider, FakeLLMProvider
from lumen.providers.results import ChatMessage, LLMUsage
from lumen.providers.telemetry import log_embedding_call, log_llm_call
from lumen.schemas.enums import EmbeddingTaskType, ModelRole

SECRET = "I have been feeling very low about the argument with my sister"


class Answer(BaseModel):
    answer: str


def provider_lines(logs, operation=None):
    """The model-call lines out of a captured log."""
    lines = [line for line in logs if line.get("logger") == "lumen.providers"]
    if operation is not None:
        lines = [line for line in lines if line.get("operation") == operation]
    return lines


class TestWhatIsRecorded:
    def test_one_line_is_written_per_call(self, provider_config, captured_logs):
        FakeLLMProvider(['{"answer": "ok"}'], config=provider_config).generate_structured(
            "q", Answer
        )
        assert len(provider_lines(captured_logs, "generate_structured")) == 1

    def test_the_provider_and_model_are_named(self, provider_config, captured_logs):
        FakeLLMProvider(['{"answer": "ok"}'], config=provider_config).generate_structured(
            "q", Answer
        )
        line = provider_lines(captured_logs)[0]
        assert line["provider"] == "fake"
        assert line["model"] == "fake-model"

    def test_the_role_is_recorded(self, provider_config, captured_logs):
        FakeLLMProvider(
            ["ok"], role=ModelRole.THINKING, config=provider_config
        ).generate_text([ChatMessage(role="user", content="hi")])
        assert provider_lines(captured_logs)[0]["model_role"] == "THINKING"

    def test_both_timings_are_recorded(self, provider_config, captured_logs):
        FakeLLMProvider(['{"answer": "ok"}'], config=provider_config).generate_structured(
            "q", Answer
        )
        line = provider_lines(captured_logs)[0]
        assert "latency_ms" in line
        assert "elapsed_ms" in line

    def test_the_token_counts_are_recorded(self, provider_config, captured_logs):
        FakeLLMProvider(["one two three"], config=provider_config).generate_text(
            [ChatMessage(role="user", content="hi")]
        )
        assert provider_lines(captured_logs)[0]["completion_tokens"] == 3

    def test_a_success_is_marked_as_such(self, provider_config, captured_logs):
        FakeLLMProvider(['{"answer": "ok"}'], config=provider_config).generate_structured(
            "q", Answer
        )
        assert provider_lines(captured_logs)[0]["outcome"] == "COMPLETE"


class TestFailuresAreRecordedToo:
    def test_a_failed_call_still_writes_a_line(self, provider_config, captured_logs):
        """A call that vanished from the log would be the hardest kind to chase."""
        with pytest.raises(Exception):
            FakeLLMProvider([], config=provider_config).generate_structured("q", Answer)
        assert len(provider_lines(captured_logs, "generate_structured")) == 1

    def test_the_failure_is_marked(self, provider_config, captured_logs):
        with pytest.raises(Exception):
            FakeLLMProvider([], config=provider_config).generate_structured("q", Answer)
        assert provider_lines(captured_logs)[0]["outcome"] == "FAILED"

    def test_what_went_wrong_is_named(self, provider_config, captured_logs):
        with pytest.raises(Exception):
            FakeLLMProvider([], config=provider_config).generate_structured("q", Answer)
        assert provider_lines(captured_logs)[0]["error_type"] == "FakeScriptExhaustedError"

    def test_a_failure_is_logged_at_error_level(self, provider_config, captured_logs):
        with pytest.raises(Exception):
            FakeLLMProvider([], config=provider_config).generate_structured("q", Answer)
        assert provider_lines(captured_logs)[0]["level"] == "ERROR"


class TestPromptsStayOut:
    def test_the_prompt_is_not_logged_by_default(self, provider_config, captured_logs):
        FakeLLMProvider(['{"answer": "ok"}'], config=provider_config).generate_structured(
            SECRET, Answer
        )
        assert all(SECRET not in str(line) for line in captured_logs)

    def test_the_answer_is_not_logged_by_default(self, provider_config, captured_logs):
        reply = '{"answer": "%s"}' % SECRET
        FakeLLMProvider([reply], config=provider_config).generate_structured("q", Answer)
        assert all(SECRET not in str(line) for line in captured_logs)

    def test_a_failed_call_does_not_leak_the_prompt_either(self, provider_config, captured_logs):
        with pytest.raises(Exception):
            FakeLLMProvider([], config=provider_config).generate_structured(SECRET, Answer)
        assert all(SECRET not in str(line) for line in captured_logs)

    def test_turning_it_on_records_the_prompt(self, captured_logs):
        """Available deliberately, for when something has to be debugged."""
        config = ProviderConfig(log_prompts=True, max_attempts=1, backoff_base_seconds=0.0)
        FakeLLMProvider(['{"answer": "ok"}'], config=config).generate_structured(SECRET, Answer)
        assert provider_lines(captured_logs)[0]["prompt"] == SECRET

    def test_turning_it_on_records_the_answer(self, captured_logs):
        config = ProviderConfig(log_prompts=True, max_attempts=1, backoff_base_seconds=0.0)
        FakeLLMProvider(['{"answer": "yes"}'], config=config).generate_structured("q", Answer)
        assert provider_lines(captured_logs)[0]["completion"] == '{"answer": "yes"}'

    def test_a_very_long_prompt_is_shortened(self, captured_logs):
        """So one entry cannot fill the log file."""
        config = ProviderConfig(log_prompts=True, max_attempts=1, backoff_base_seconds=0.0)
        long_prompt = "x" * 5000
        FakeLLMProvider(['{"answer": "ok"}'], config=config).generate_structured(
            long_prompt, Answer
        )
        recorded = provider_lines(captured_logs)[0]["prompt"]
        assert len(recorded) < 5000
        assert "more characters" in recorded


class TestEmbeddingLines:
    def test_a_line_is_written(self, provider_config, captured_logs):
        FakeEmbeddingProvider(dimensions=16, config=provider_config).embed_batch(["a", "b"])
        assert len(provider_lines(captured_logs, "embed_batch")) == 1

    def test_how_many_texts_went_in_is_recorded(self, provider_config, captured_logs):
        FakeEmbeddingProvider(dimensions=16, config=provider_config).embed_batch(["a", "b", "c"])
        assert provider_lines(captured_logs, "embed_batch")[0]["text_count"] == 3

    def test_the_task_is_recorded(self, provider_config, captured_logs):
        FakeEmbeddingProvider(dimensions=16, config=provider_config).embed_text(
            "a", task_type=EmbeddingTaskType.QUERY
        )
        assert provider_lines(captured_logs, "embed_batch")[0]["task_type"] == "QUERY"

    def test_the_embedded_text_is_never_logged(self, provider_config, captured_logs):
        """Same reason as prompts: it is the journal."""
        FakeEmbeddingProvider(dimensions=16, config=provider_config).embed_text(SECRET)
        assert all(SECRET not in str(line) for line in captured_logs)


class TestTheLoggingFunctionsDirectly:
    def test_usage_is_left_out_when_unknown(self, captured_logs):
        log_llm_call(
            provider="p",
            model="m",
            role=ModelRole.LIGHTWEIGHT,
            operation="op",
            outcome="COMPLETE",
            latency_ms=1,
            elapsed_ms=1,
        )
        assert "prompt_tokens" not in provider_lines(captured_logs)[0]

    def test_usage_is_included_when_known(self, captured_logs):
        log_llm_call(
            provider="p",
            model="m",
            role=ModelRole.LIGHTWEIGHT,
            operation="op",
            outcome="COMPLETE",
            latency_ms=1,
            elapsed_ms=1,
            usage=LLMUsage(prompt_tokens=5, completion_tokens=2, total_tokens=7),
        )
        assert provider_lines(captured_logs)[0]["total_tokens"] == 7

    def test_extra_details_can_be_attached(self, captured_logs):
        log_llm_call(
            provider="p",
            model="m",
            role=ModelRole.LIGHTWEIGHT,
            operation="op",
            outcome="COMPLETE",
            latency_ms=1,
            elapsed_ms=1,
            extra={"anything": "here"},
        )
        assert provider_lines(captured_logs)[0]["anything"] == "here"

    def test_the_stop_reason_is_included_when_known(self, captured_logs):
        log_llm_call(
            provider="p",
            model="m",
            role=ModelRole.LIGHTWEIGHT,
            operation="op",
            outcome="COMPLETE",
            latency_ms=1,
            elapsed_ms=1,
            finish_reason="STOP",
        )
        assert provider_lines(captured_logs)[0]["finish_reason"] == "STOP"

    def test_an_embedding_failure_is_marked(self, captured_logs):
        log_embedding_call(
            provider="p",
            model="m",
            operation="embed_batch",
            outcome="FAILED",
            elapsed_ms=1,
            text_count=2,
            task_type="DOCUMENT",
            error_type="ProviderTimeoutError",
        )
        line = provider_lines(captured_logs)[0]
        assert line["level"] == "ERROR"
        assert line["error_type"] == "ProviderTimeoutError"
