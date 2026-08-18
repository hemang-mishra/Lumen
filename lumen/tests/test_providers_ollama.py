"""
Tests for the Ollama provider.

Same approach as the Gemini tests: the client is handed in, so nothing here
needs a running daemon. The interesting differences are that Ollama has no
separate field for a system instruction, and that its two most common failures
deserve messages that say what to actually do about them.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from lumen.config import ProviderConfig
from lumen.providers.errors import (
    ProviderConfigurationError,
    ProviderError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from lumen.providers.ollama import OllamaEmbeddingProvider, OllamaLLMProvider
from lumen.providers.results import ChatMessage
from lumen.schemas.enums import EmbeddingTaskType, ModelRole


class Answer(BaseModel):
    """A small shape for structured-output tests."""

    answer: str


def chat_reply(text: str = '{"answer": "yes"}', *, prompt_tokens=10, eval_tokens=4, done_reason="stop"):
    """Build something shaped like an Ollama chat reply."""
    return SimpleNamespace(
        message=SimpleNamespace(content=text),
        prompt_eval_count=prompt_tokens,
        eval_count=eval_tokens,
        done_reason=done_reason,
    )


class FakeOllamaClient:
    """Stands in for ollama.Client, remembering how it was called."""

    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls: list[dict] = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result if self.result is not None else chat_reply()

    def embed(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


def build_provider(config: ProviderConfig, result=None, error=None, model="llama3.3"):
    client = FakeOllamaClient(result=result, error=error)
    provider = OllamaLLMProvider(model, ModelRole.LIGHTWEIGHT, config, client=client)
    return provider, client


def response_error(status: int, message: str = "boom"):
    """Build the SDK's error type with a status code on it."""
    import ollama

    error = ollama.ResponseError(message)
    error.status_code = status
    return error


class TestStructuredRequests:
    def test_the_shape_is_sent_as_a_schema(self, provider_config):
        provider, client = build_provider(provider_config)
        provider.generate_structured("question", Answer)
        assert client.calls[0]["format"] == Answer.model_json_schema()

    def test_a_conversation_sends_no_schema(self, provider_config):
        provider, client = build_provider(provider_config)
        provider.generate_text([ChatMessage(role="user", content="hi")])
        assert client.calls[0]["format"] is None

    def test_the_temperature_is_passed_as_an_option(self, provider_config):
        provider, client = build_provider(provider_config)
        provider.generate_structured("question", Answer)
        assert client.calls[0]["options"]["temperature"] == provider_config.temperature

    def test_the_model_name_is_sent(self, provider_config):
        provider, client = build_provider(provider_config, model="phi-3")
        provider.generate_structured("question", Answer)
        assert client.calls[0]["model"] == "phi-3"


class TestSystemInstructions:
    def test_it_becomes_the_first_message(self, provider_config):
        """Ollama has no separate field for it, so it goes in as a message."""
        provider, client = build_provider(provider_config)
        provider.generate_structured("question", Answer, system_instruction="be terse")
        assert client.calls[0]["messages"][0] == {"role": "system", "content": "be terse"}

    def test_the_prompt_follows_it(self, provider_config):
        provider, client = build_provider(provider_config)
        provider.generate_structured("question", Answer, system_instruction="be terse")
        assert client.calls[0]["messages"][1] == {"role": "user", "content": "question"}

    def test_no_instruction_means_no_extra_message(self, provider_config):
        provider, client = build_provider(provider_config)
        provider.generate_structured("question", Answer)
        assert len(client.calls[0]["messages"]) == 1


class TestReadingReplies:
    def test_the_parsed_object_comes_back(self, provider_config):
        provider, _ = build_provider(provider_config, result=chat_reply('{"answer": "yes"}'))
        assert provider.generate_structured("q", Answer).data == {"answer": "yes"}

    def test_the_token_counts_are_read(self, provider_config):
        provider, _ = build_provider(
            provider_config, result=chat_reply(prompt_tokens=30, eval_tokens=8)
        )
        usage = provider.generate_structured("q", Answer).usage
        assert (usage.prompt_tokens, usage.completion_tokens, usage.total_tokens) == (30, 8, 38)

    def test_missing_counts_read_as_unknown(self, provider_config):
        provider, _ = build_provider(
            provider_config, result=chat_reply(prompt_tokens=None, eval_tokens=None)
        )
        assert provider.generate_structured("q", Answer).usage.total_tokens is None

    def test_the_stop_reason_is_reported(self, provider_config):
        provider, _ = build_provider(provider_config, result=chat_reply(done_reason="stop"))
        assert provider.generate_structured("q", Answer).finish_reason == "stop"

    def test_a_reply_given_as_a_dictionary_is_understood(self, provider_config):
        """Older versions of the library hand back plain dictionaries."""
        reply = {
            "message": {"content": '{"answer": "yes"}'},
            "prompt_eval_count": 5,
            "eval_count": 2,
            "done_reason": "stop",
        }
        provider, _ = build_provider(provider_config, result=reply)
        assert provider.generate_structured("q", Answer).data == {"answer": "yes"}

    def test_an_empty_reply_is_refused(self, provider_config):
        provider, _ = build_provider(provider_config, result=chat_reply(text=""))
        with pytest.raises(ProviderResponseError, match="no text"):
            provider.generate_structured("q", Answer)

    def test_a_reply_with_no_message_is_refused(self, provider_config):
        provider, _ = build_provider(provider_config, result=SimpleNamespace(message=None))
        with pytest.raises(ProviderResponseError, match="no text"):
            provider.generate_structured("q", Answer)


class TestErrorTranslation:
    def test_a_missing_model_says_how_to_get_it(self, provider_config):
        """The most common local mistake, so the message names the command."""
        provider, _ = build_provider(provider_config, error=response_error(404), model="phi-3")
        with pytest.raises(ProviderConfigurationError, match="ollama pull phi-3"):
            provider.generate_structured("q", Answer)

    def test_a_missing_model_is_not_retried(self, provider_config):
        provider, client = build_provider(provider_config, error=response_error(404))
        with pytest.raises(ProviderConfigurationError):
            provider.generate_structured("q", Answer)
        assert len(client.calls) == 1

    def test_a_refused_connection_names_the_host(self, provider_config):
        """The daemon not running is the likeliest cause, so it is said outright."""
        config = ProviderConfig(
            ollama_host="http://localhost:9999",
            max_attempts=1,
            backoff_base_seconds=0.0,
        )
        provider, _ = build_provider(config, error=ConnectionError("refused"))
        with pytest.raises(ProviderUnavailableError, match="localhost:9999"):
            provider.generate_structured("q", Answer)

    def test_a_refused_connection_mentions_the_daemon(self, provider_config):
        provider, _ = build_provider(provider_config, error=ConnectionError("refused"))
        with pytest.raises(ProviderUnavailableError, match="daemon"):
            provider.generate_structured("q", Answer)

    def test_a_server_problem_can_be_retried(self, provider_config):
        provider, client = build_provider(provider_config, error=response_error(500))
        with pytest.raises(ProviderUnavailableError):
            provider.generate_structured("q", Answer)
        assert len(client.calls) == provider_config.max_attempts

    def test_a_rejected_request_is_not_retried(self, provider_config):
        provider, client = build_provider(provider_config, error=response_error(400))
        with pytest.raises(ProviderResponseError):
            provider.generate_structured("q", Answer)
        assert len(client.calls) == 1

    def test_a_timeout_becomes_a_timeout(self, provider_config):
        import httpx

        provider, _ = build_provider(provider_config, error=httpx.ReadTimeout("slow"))
        with pytest.raises(ProviderTimeoutError):
            provider.generate_structured("q", Answer)

    def test_the_library_s_own_request_error_means_unreachable(self, provider_config):
        import ollama

        provider, _ = build_provider(provider_config, error=ollama.RequestError("bad host"))
        with pytest.raises(ProviderUnavailableError):
            provider.generate_structured("q", Answer)

    def test_anything_unexpected_still_becomes_a_provider_error(self, provider_config):
        provider, _ = build_provider(provider_config, error=RuntimeError("odd"))
        with pytest.raises(ProviderError):
            provider.generate_structured("q", Answer)


class TestEmbedding:
    def _reply(self, count: int, dimensions: int = 768):
        return SimpleNamespace(embeddings=[[0.1] * dimensions for _ in range(count)])

    def _provider(self, config, result=None, error=None, model="nomic-embed-text"):
        client = FakeOllamaClient(result=result, error=error)
        return OllamaEmbeddingProvider(model, config, client=client), client

    def test_the_vector_width_is_known_from_the_model(self, provider_config):
        provider, _ = self._provider(provider_config, result=self._reply(1))
        assert provider.dimensions == 768

    def test_the_task_is_added_as_a_prefix(self, provider_config):
        """
        Ollama has no field for the task, but the model was trained expecting a
        short prefix, so the provider adds it.
        """
        provider, client = self._provider(provider_config, result=self._reply(1))
        provider.embed_text("hello", task_type=EmbeddingTaskType.QUERY)
        assert client.calls[0]["input"] == ["search_query: hello"]

    @pytest.mark.parametrize(
        "task,prefix",
        [
            (EmbeddingTaskType.DOCUMENT, "search_document: "),
            (EmbeddingTaskType.QUERY, "search_query: "),
            (EmbeddingTaskType.SIMILARITY, "clustering: "),
            (EmbeddingTaskType.CLASSIFICATION, "classification: "),
        ],
    )
    def test_every_task_has_its_own_prefix(self, provider_config, task, prefix):
        provider, client = self._provider(provider_config, result=self._reply(1))
        provider.embed_text("hello", task_type=task)
        assert client.calls[0]["input"] == [f"{prefix}hello"]

    def test_the_unprefixed_text_never_reaches_the_server(self, provider_config):
        provider, client = self._provider(provider_config, result=self._reply(1))
        provider.embed_text("hello")
        assert "hello" not in client.calls[0]["input"]

    def test_a_version_tag_still_finds_the_prefixes(self, provider_config):
        provider, client = self._provider(
            provider_config, result=self._reply(1), model="nomic-embed-text:v1.5"
        )
        provider.embed_text("hello", task_type=EmbeddingTaskType.QUERY)
        assert client.calls[0]["input"] == ["search_query: hello"]

    def test_an_unknown_model_gets_no_prefix(self, provider_config):
        """
        Prefixes belong to a particular family of models. Putting them in front
        of a model that never saw them would spoil every vector, which is worse
        than leaving search slightly weaker.
        """
        provider, client = self._provider(
            provider_config, result=self._reply(1), model="mxbai-embed-large"
        )
        provider.embed_text("hello")
        assert client.calls[0]["input"] == ["hello"]

    def test_an_unknown_model_is_warned_about(self, provider_config, captured_logs):
        provider, _ = self._provider(
            provider_config, result=self._reply(1), model="mxbai-embed-large"
        )
        provider.embed_text("hello")
        warnings = [line for line in captured_logs if "task prefixes" in line["msg"]]
        assert len(warnings) == 1

    def test_the_warning_is_only_given_once(self, provider_config, captured_logs):
        """One line per unusual setup, not one per journal entry."""
        provider, _ = self._provider(
            provider_config, result=self._reply(3), model="mxbai-embed-large"
        )
        provider.embed_batch(["a", "b", "c"])
        provider.embed_batch(["d", "e", "f"])
        warnings = [line for line in captured_logs if "task prefixes" in line["msg"]]
        assert len(warnings) == 1

    def test_a_batch_is_sent_in_one_call(self, provider_config):
        """Ollama takes a list, so there is no need to split it up."""
        provider, client = self._provider(provider_config, result=self._reply(3))
        provider.embed_batch(["a", "b", "c"])
        assert len(client.calls) == 1

    def test_a_short_reply_is_refused(self, provider_config):
        provider, _ = self._provider(provider_config, result=self._reply(2))
        with pytest.raises(ProviderResponseError, match="asked for 3"):
            provider.embed_batch(["a", "b", "c"])

    def test_a_reply_given_as_a_dictionary_is_understood(self, provider_config):
        provider, _ = self._provider(provider_config, result={"embeddings": [[0.5] * 768]})
        assert len(provider.embed_text("hello")) == 768

    def test_failures_are_translated_here_too(self, provider_config):
        provider, _ = self._provider(provider_config, error=ConnectionError("refused"))
        with pytest.raises(ProviderUnavailableError):
            provider.embed_text("hello")


def stream_piece(text: str = "", *, done: bool = False, eval_tokens: int = 0):
    """Build something shaped like one piece of an Ollama stream."""
    return SimpleNamespace(
        message=SimpleNamespace(content=text),
        done=done,
        done_reason="stop" if done else None,
        prompt_eval_count=10 if done else None,
        eval_count=eval_tokens if done else None,
    )


class TestStreamingAReply:
    """
    A local model sending its answer as it writes it.

    The shape differs from a finished reply in one way that matters: the
    token counts only arrive on the piece marked done, so reading them from
    every piece would overwrite real numbers with nothing.
    """

    def test_the_pieces_join_up(self, provider_config):
        pieces = [stream_piece("Hello "), stream_piece("there"), stream_piece(done=True)]
        provider, _ = build_provider(provider_config, result=pieces)

        chunks = list(provider.stream_text([ChatMessage(role="user", content="hi")]))

        assert "".join(chunk.text for chunk in chunks) == "Hello there"

    def test_it_asks_for_a_stream(self, provider_config):
        provider, client = build_provider(provider_config, result=[stream_piece(done=True)])

        list(provider.stream_text([ChatMessage(role="user", content="hi")]))

        assert client.calls[0]["stream"] is True

    def test_the_totals_come_from_the_piece_that_says_it_is_done(self, provider_config):
        pieces = [stream_piece("Hello"), stream_piece(done=True, eval_tokens=7)]
        provider, _ = build_provider(provider_config, result=pieces)

        final = list(provider.stream_text([ChatMessage(role="user", content="hi")]))[-1]

        assert final.final is True
        assert final.usage.completion_tokens == 7
        assert final.finish_reason == "stop"

    def test_a_system_instruction_goes_in_as_the_first_message(self, provider_config):
        """Ollama has no separate field for one, so it becomes a message."""
        provider, client = build_provider(provider_config, result=[stream_piece(done=True)])

        list(
            provider.stream_text(
                [ChatMessage(role="user", content="hi")],
                system_instruction="be warm",
            )
        )

        assert client.calls[0]["messages"][0] == {
            "role": "system",
            "content": "be warm",
        }

    def test_a_daemon_that_is_not_running_is_reported_properly(self, provider_config):
        provider, _ = build_provider(
            provider_config, error=ConnectionError("connection refused")
        )

        with pytest.raises(ProviderError):
            list(provider.stream_text([ChatMessage(role="user", content="hi")]))
