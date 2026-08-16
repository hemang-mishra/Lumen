"""
Tests for the Gemini provider.

The client is handed in rather than built, so these run with no network and no
credential. What is being checked is the part that is genuinely ours: how a
request is shaped, and how a reply is taken apart — including the replies that
arrived but cannot be used.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict

from lumen.config import ProviderConfig
from lumen.providers.errors import (
    ProviderConfigurationError,
    ProviderContentBlockedError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from lumen.providers.gemini import GeminiEmbeddingProvider, GeminiLLMProvider
from lumen.providers.results import ChatMessage
from lumen.schemas.enums import EmbeddingTaskType, ModelRole


class Answer(BaseModel):
    """A small shape for structured-output tests."""

    answer: str
    score: float | None = None


def reply(
    text: str = '{"answer": "yes"}',
    *,
    finish_reason: str = "STOP",
    block_reason=None,
    prompt_tokens: int | None = 12,
    completion_tokens: int | None = 5,
    safety_ratings=(),
):
    """Build something shaped like an SDK reply."""
    candidate = SimpleNamespace(
        finish_reason=finish_reason,
        safety_ratings=list(safety_ratings),
    )
    usage = None
    if prompt_tokens is not None or completion_tokens is not None:
        usage = SimpleNamespace(
            prompt_token_count=prompt_tokens,
            candidates_token_count=completion_tokens,
            total_token_count=(prompt_tokens or 0) + (completion_tokens or 0),
        )
    return SimpleNamespace(
        text=text,
        candidates=[candidate],
        usage_metadata=usage,
        prompt_feedback=SimpleNamespace(block_reason=block_reason),
    )


class FakeModels:
    """Stands in for client.models, remembering how it was called."""

    def __init__(self, result=None, error=None):
        self.result = result if result is not None else reply()
        self.error = error
        self.calls: list[dict] = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result

    def embed_content(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


def build_client(result=None, error=None):
    models = FakeModels(result=result, error=error)
    return SimpleNamespace(models=models), models


def build_provider(config: ProviderConfig, result=None, error=None, role=ModelRole.LIGHTWEIGHT):
    client, models = build_client(result=result, error=error)
    provider = GeminiLLMProvider("gemini-2.5-flash", role, config, client=client)
    return provider, models


class TestCredentials:
    def test_a_missing_credential_is_refused_at_construction(self, monkeypatch, provider_config):
        """
        Caught while starting up rather than surfacing as a rejected call in the
        middle of a pipeline run.
        """
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        with pytest.raises(ProviderConfigurationError, match="GEMINI_API_KEY"):
            GeminiLLMProvider("gemini-2.5-flash", ModelRole.LIGHTWEIGHT, provider_config)

    def test_a_supplied_client_needs_no_credential(self, monkeypatch, provider_config):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        provider, _ = build_provider(provider_config)
        assert provider.provider_name == "gemini"


class NestedAnswer(BaseModel):
    """A shape with a nested one inside it, since that is where schemas nest."""

    answers: list[Answer]


class TestStructuredRequests:
    def test_the_shape_is_taken_from_the_model_class(self, provider_config):
        """Nobody writes a JSON schema by hand, so it cannot drift from the code."""
        provider, models = build_provider(provider_config)
        provider.generate_structured("question", Answer)
        sent = models.calls[0]["config"].response_schema
        assert sent["properties"].keys() == {"answer", "score"}

    def test_the_key_gemini_refuses_is_left_out(self, provider_config):
        """
        Every contract forbids unexpected fields, which Pydantic writes as
        `additionalProperties`. Gemini rejects a request that names it, so
        sending it fails every structured call on every model.
        """

        class Strict(BaseModel):
            model_config = ConfigDict(extra="forbid")

            answer: str

        provider, models = build_provider(provider_config)
        provider.generate_structured("question", Strict)
        assert "additionalProperties" not in json.dumps(
            models.calls[0]["config"].response_schema
        )

    def test_it_is_left_out_of_nested_shapes_too(self, provider_config):
        """The rejection names the nested field as readily as the outer one."""

        class Strict(BaseModel):
            model_config = ConfigDict(extra="forbid")

            answer: str

        class Wrapper(BaseModel):
            model_config = ConfigDict(extra="forbid")

            answers: list[Strict]

        provider, models = build_provider(provider_config)
        provider.generate_structured("question", Wrapper)
        assert "additionalProperties" not in json.dumps(
            models.calls[0]["config"].response_schema
        )

    def test_a_nested_shape_still_describes_itself(self, provider_config):
        """Stripping one key must not flatten the schema into uselessness."""
        provider, models = build_provider(provider_config)
        provider.generate_structured("question", NestedAnswer)
        sent = models.calls[0]["config"].response_schema
        assert "Answer" in sent["$defs"]
        assert sent["$defs"]["Answer"]["properties"].keys() == {"answer", "score"}

    def test_json_output_is_requested(self, provider_config):
        provider, models = build_provider(provider_config)
        provider.generate_structured("question", Answer)
        assert models.calls[0]["config"].response_mime_type == "application/json"

    def test_the_configured_temperature_is_used(self, provider_config):
        provider, models = build_provider(provider_config)
        provider.generate_structured("question", Answer)
        assert models.calls[0]["config"].temperature == provider_config.temperature

    def test_a_caller_can_override_the_temperature(self, provider_config):
        provider, models = build_provider(provider_config)
        provider.generate_structured("question", Answer, temperature=0.9)
        assert models.calls[0]["config"].temperature == 0.9

    def test_a_system_instruction_is_passed_along(self, provider_config):
        provider, models = build_provider(provider_config)
        provider.generate_structured("question", Answer, system_instruction="be terse")
        assert models.calls[0]["config"].system_instruction == "be terse"

    def test_the_model_name_is_sent(self, provider_config):
        provider, models = build_provider(provider_config)
        provider.generate_structured("question", Answer)
        assert models.calls[0]["model"] == "gemini-2.5-flash"

    def test_deep_reasoning_gets_a_longer_time_budget(self, provider_config):
        fast, fast_models = build_provider(provider_config, role=ModelRole.LIGHTWEIGHT)
        deep, deep_models = build_provider(provider_config, role=ModelRole.THINKING)
        fast.generate_structured("q", Answer)
        deep.generate_structured("q", Answer)
        assert deep_models.calls[0]["config"].http_options.timeout > (
            fast_models.calls[0]["config"].http_options.timeout
        )


class TestSafetySettings:
    def test_the_filters_are_turned_down(self, provider_config):
        """
        Journal entries discuss self-harm and conflict, and those are the entries
        most worth keeping. Left at their defaults, the filters would quietly
        drop them.
        """
        provider, models = build_provider(provider_config)
        provider.generate_structured("question", Answer)
        settings = models.calls[0]["config"].safety_settings
        assert len(settings) == 4
        assert all(setting.threshold == "BLOCK_NONE" for setting in settings)

    def test_the_settings_are_also_applied_to_conversations(self, provider_config):
        provider, models = build_provider(provider_config)
        provider.generate_text([ChatMessage(role="user", content="hello")])
        assert len(models.calls[0]["config"].safety_settings) == 4


class TestReadingReplies:
    def test_the_parsed_object_comes_back(self, provider_config):
        provider, _ = build_provider(provider_config, result=reply('{"answer": "yes"}'))
        assert provider.generate_structured("q", Answer).data == {"answer": "yes"}

    def test_the_original_text_is_kept(self, provider_config):
        provider, _ = build_provider(provider_config, result=reply('{"answer": "yes"}'))
        assert provider.generate_structured("q", Answer).text == '{"answer": "yes"}'

    def test_the_token_counts_are_read(self, provider_config):
        provider, _ = build_provider(provider_config, result=reply(prompt_tokens=40, completion_tokens=7))
        usage = provider.generate_structured("q", Answer).usage
        assert (usage.prompt_tokens, usage.completion_tokens, usage.total_tokens) == (40, 7, 47)

    def test_a_reply_with_no_counts_is_fine(self, provider_config):
        provider, _ = build_provider(
            provider_config, result=reply(prompt_tokens=None, completion_tokens=None)
        )
        assert provider.generate_structured("q", Answer).usage.total_tokens is None

    def test_the_stop_reason_is_reported(self, provider_config):
        provider, _ = build_provider(provider_config, result=reply(finish_reason="STOP"))
        assert provider.generate_structured("q", Answer).finish_reason == "STOP"

    def test_the_provider_and_model_are_named_in_the_result(self, provider_config):
        provider, _ = build_provider(provider_config)
        result = provider.generate_structured("q", Answer)
        assert (result.provider, result.model) == ("gemini", "gemini-2.5-flash")


class TestUnreadableJson:
    def test_bad_json_is_reported_rather_than_raised(self, provider_config):
        """
        The layer that knows what the data should mean decides whether to ask
        again, and it needs the original text to say what was wrong.
        """
        provider, _ = build_provider(provider_config, result=reply("this is not json"))
        result = provider.generate_structured("q", Answer)
        assert result.data is None
        assert result.parse_error is not None
        assert result.text == "this is not json"

    def test_bad_json_is_not_retried(self, provider_config):
        """The model answered. Asking the same way gets the same answer."""
        provider, models = build_provider(provider_config, result=reply("not json"))
        provider.generate_structured("q", Answer)
        assert len(models.calls) == 1

    def test_a_json_list_is_treated_as_wrong(self, provider_config):
        provider, _ = build_provider(provider_config, result=reply('["a", "b"]'))
        result = provider.generate_structured("q", Answer)
        assert result.data is None
        assert "expected a JSON object" in result.parse_error

    def test_valid_json_reports_that_it_parsed(self, provider_config):
        provider, _ = build_provider(provider_config)
        assert provider.generate_structured("q", Answer).parsed_ok is True


class TestRefusedContent:
    def test_a_refused_prompt_raises_its_own_error(self, provider_config):
        provider, _ = build_provider(provider_config, result=reply(block_reason="SAFETY"))
        with pytest.raises(ProviderContentBlockedError):
            provider.generate_structured("a hard entry", Answer)

    def test_a_refused_answer_raises_its_own_error(self, provider_config):
        provider, _ = build_provider(provider_config, result=reply(finish_reason="SAFETY"))
        with pytest.raises(ProviderContentBlockedError):
            provider.generate_structured("a hard entry", Answer)

    def test_a_refusal_is_never_retried(self, provider_config):
        """Retrying would fail the same way every time and waste the quota."""
        provider, models = build_provider(provider_config, result=reply(finish_reason="SAFETY"))
        with pytest.raises(ProviderContentBlockedError):
            provider.generate_structured("a hard entry", Answer)
        assert len(models.calls) == 1

    def test_the_triggered_categories_are_reported(self, provider_config):
        ratings = [SimpleNamespace(category="HARM_CATEGORY_DANGEROUS_CONTENT", blocked=True)]
        provider, _ = build_provider(
            provider_config, result=reply(finish_reason="SAFETY", safety_ratings=ratings)
        )
        with pytest.raises(ProviderContentBlockedError) as caught:
            provider.generate_structured("q", Answer)
        assert "HARM_CATEGORY_DANGEROUS_CONTENT" in caught.value.blocked_categories

    def test_ratings_that_did_not_trigger_are_left_out(self, provider_config):
        ratings = [SimpleNamespace(category="HARM_CATEGORY_HATE_SPEECH", blocked=False)]
        provider, _ = build_provider(
            provider_config, result=reply(finish_reason="SAFETY", safety_ratings=ratings)
        )
        with pytest.raises(ProviderContentBlockedError) as caught:
            provider.generate_structured("q", Answer)
        assert caught.value.blocked_categories == ()


class TestUnusableReplies:
    def test_output_cut_short_is_refused(self, provider_config):
        """Half a JSON object cannot be fixed by asking more nicely."""
        provider, _ = build_provider(provider_config, result=reply(finish_reason="MAX_TOKENS"))
        with pytest.raises(ProviderResponseError, match="cut short"):
            provider.generate_structured("q", Answer)

    def test_an_empty_reply_is_refused(self, provider_config):
        provider, _ = build_provider(provider_config, result=reply(text=""))
        with pytest.raises(ProviderResponseError, match="no text"):
            provider.generate_structured("q", Answer)


class TestErrorTranslation:
    def _api_error(self, code: int, message: str = "boom", headers=None):
        from google.genai import errors as genai_errors

        response = SimpleNamespace(headers=headers or {})
        error = genai_errors.APIError(code, {"error": {"message": message}}, response)
        return error

    @pytest.mark.parametrize(
        "code,expected",
        [
            (429, ProviderRateLimitError),
            (401, ProviderConfigurationError),
            (403, ProviderConfigurationError),
            (404, ProviderConfigurationError),
            (500, ProviderUnavailableError),
            (503, ProviderUnavailableError),
            (400, ProviderResponseError),
        ],
    )
    def test_status_codes_become_the_right_error(self, provider_config, code, expected):
        provider, _ = build_provider(provider_config, error=self._api_error(code))
        with pytest.raises(expected):
            provider.generate_structured("q", Answer)

    def test_the_wait_a_rate_limit_asked_for_is_carried_over(self, provider_config):
        error = self._api_error(429, headers={"Retry-After": "17"})
        provider, _ = build_provider(provider_config, error=error)
        with pytest.raises(ProviderRateLimitError) as caught:
            provider.generate_structured("q", Answer)
        assert caught.value.retry_after_seconds == 17.0

    def test_an_unreadable_retry_header_is_ignored(self, provider_config):
        error = self._api_error(429, headers={"Retry-After": "soon"})
        provider, _ = build_provider(provider_config, error=error)
        with pytest.raises(ProviderRateLimitError) as caught:
            provider.generate_structured("q", Answer)
        assert caught.value.retry_after_seconds is None

    def test_a_timeout_becomes_a_timeout(self, provider_config):
        import httpx

        provider, _ = build_provider(provider_config, error=httpx.ReadTimeout("timed out"))
        with pytest.raises(ProviderTimeoutError):
            provider.generate_structured("q", Answer)

    def test_a_connection_problem_becomes_unavailable(self, provider_config):
        import httpx

        provider, _ = build_provider(provider_config, error=httpx.ConnectError("refused"))
        with pytest.raises(ProviderUnavailableError):
            provider.generate_structured("q", Answer)

    def test_anything_unexpected_still_becomes_a_provider_error(self, provider_config):
        from lumen.providers.errors import ProviderError

        provider, _ = build_provider(provider_config, error=RuntimeError("odd"))
        with pytest.raises(ProviderError):
            provider.generate_structured("q", Answer)

    def test_recoverable_failures_are_retried(self, provider_config):
        provider, models = build_provider(provider_config, error=self._api_error(503))
        with pytest.raises(ProviderUnavailableError):
            provider.generate_structured("q", Answer)
        assert len(models.calls) == provider_config.max_attempts

    def test_setup_problems_are_not_retried(self, provider_config):
        provider, models = build_provider(provider_config, error=self._api_error(401))
        with pytest.raises(ProviderConfigurationError):
            provider.generate_structured("q", Answer)
        assert len(models.calls) == 1


class TestConversations:
    def test_the_text_comes_back(self, provider_config):
        provider, _ = build_provider(provider_config, result=reply(text="hello there"))
        result = provider.generate_text([ChatMessage(role="user", content="hi")])
        assert result.text == "hello there"

    def test_the_assistant_is_renamed_to_what_the_sdk_expects(self, provider_config):
        provider, models = build_provider(provider_config, result=reply(text="ok"))
        provider.generate_text(
            [
                ChatMessage(role="user", content="hi"),
                ChatMessage(role="assistant", content="hello"),
                ChatMessage(role="user", content="how are you"),
            ]
        )
        roles = [content.role for content in models.calls[0]["contents"]]
        assert roles == ["user", "model", "user"]

    def test_a_system_message_is_sent_as_user_text(self, provider_config):
        """Gemini has no system role in the message list, so it is folded in."""
        provider, models = build_provider(provider_config, result=reply(text="ok"))
        provider.generate_text([ChatMessage(role="system", content="be terse")])
        assert models.calls[0]["contents"][0].role == "user"

    def test_an_empty_conversation_is_rejected(self, provider_config):
        provider, _ = build_provider(provider_config)
        with pytest.raises(ValueError, match="must not be empty"):
            provider.generate_text([])


class TestEmbedding:
    def _embedding_reply(self, count: int, dimensions: int = 768):
        return SimpleNamespace(
            embeddings=[
                SimpleNamespace(values=[0.1] * dimensions) for _ in range(count)
            ]
        )

    def _provider(self, provider_config, result=None, error=None, model="text-embedding-004"):
        client, models = build_client(result=result, error=error)
        return GeminiEmbeddingProvider(model, provider_config, client=client), models

    def test_the_vector_width_is_known_from_the_model(self, provider_config):
        provider, _ = self._provider(provider_config, result=self._embedding_reply(1))
        assert provider.dimensions == 768

    def test_one_text_gives_one_vector(self, provider_config):
        provider, _ = self._provider(provider_config, result=self._embedding_reply(1))
        assert len(provider.embed_text("hello")) == 768

    def test_the_task_is_sent_to_the_api(self, provider_config):
        provider, models = self._provider(provider_config, result=self._embedding_reply(1))
        provider.embed_text("hello", task_type=EmbeddingTaskType.QUERY)
        assert models.calls[0]["config"].task_type == "RETRIEVAL_QUERY"

    @pytest.mark.parametrize(
        "task,expected",
        [
            (EmbeddingTaskType.DOCUMENT, "RETRIEVAL_DOCUMENT"),
            (EmbeddingTaskType.QUERY, "RETRIEVAL_QUERY"),
            (EmbeddingTaskType.SIMILARITY, "SEMANTIC_SIMILARITY"),
            (EmbeddingTaskType.CLASSIFICATION, "CLASSIFICATION"),
        ],
    )
    def test_every_task_has_an_api_name(self, provider_config, task, expected):
        provider, models = self._provider(provider_config, result=self._embedding_reply(1))
        provider.embed_text("hello", task_type=task)
        assert models.calls[0]["config"].task_type == expected

    def test_the_text_is_sent_unchanged(self, provider_config):
        """Gemini takes the task as a parameter, so nothing is added to the text."""
        provider, models = self._provider(provider_config, result=self._embedding_reply(1))
        provider.embed_text("hello", task_type=EmbeddingTaskType.QUERY)
        assert models.calls[0]["contents"] == ["hello"]

    def test_a_short_reply_is_refused(self, provider_config):
        """Fewer vectors than texts would silently mismatch ids and vectors."""
        provider, _ = self._provider(provider_config, result=self._embedding_reply(1))
        with pytest.raises(ProviderResponseError, match="asked for 2"):
            provider.embed_batch(["one", "two"])

    def test_a_missing_credential_is_refused(self, monkeypatch, provider_config):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        with pytest.raises(ProviderConfigurationError):
            GeminiEmbeddingProvider("text-embedding-004", provider_config)

    def test_failures_are_translated_here_too(self, provider_config):
        from google.genai import errors as genai_errors

        error = genai_errors.APIError(429, {"error": {"message": "slow down"}}, None)
        provider, _ = self._provider(provider_config, error=error)
        with pytest.raises(ProviderRateLimitError):
            provider.embed_text("hello")


class RecordingSdk:
    """
    A stand-in for the google SDK that records which key built which client.

    Only Client is faked — the types and error classes are the real ones, so
    request shaping still goes through the same code a live call would.
    """

    def __init__(self, result=None, errors=None):
        self.result = result if result is not None else reply()
        # One error per call, consumed in order; None means "this one works".
        self.errors = list(errors or [])
        self.keys_built: list[str] = []
        self.keys_used: list[str] = []

    def Client(self, *, api_key):  # noqa: N802 - matching the SDK's own name
        self.keys_built.append(api_key)
        sdk = self

        class Models:
            def generate_content(self, **kwargs):
                return sdk._respond(api_key)

            def embed_content(self, **kwargs):
                return sdk._respond(api_key)

        return SimpleNamespace(models=Models())

    def _respond(self, api_key):
        self.keys_used.append(api_key)
        if self.errors:
            error = self.errors.pop(0)
            if error is not None:
                raise error
        return self.result

    def install(self, monkeypatch):
        from google.genai import errors as genai_errors
        from google.genai import types as genai_types

        monkeypatch.setattr(
            "lumen.providers.gemini._import_sdk",
            lambda: (self, genai_types, genai_errors),
        )
        return self


class TestKeyRotation:
    """
    Quotas are metered per key. Several keys are several meters, and rotation
    is the only thing that turns the second one into extra capacity.
    """

    def build(self, monkeypatch, provider_config, keys, *, errors=None, result=None):
        monkeypatch.setenv("GEMINI_API_KEYS", ",".join(keys))
        sdk = RecordingSdk(result=result, errors=errors).install(monkeypatch)
        provider = GeminiLLMProvider(
            "gemini-2.5-flash", ModelRole.LIGHTWEIGHT, provider_config, client=None
        )
        return provider, sdk

    def test_requests_are_spread_over_every_configured_key(
        self, monkeypatch, provider_config
    ):
        monkeypatch.setenv("LUMEN_KEY_ROTATION_STRATEGY", "round_robin")
        provider, sdk = self.build(monkeypatch, ProviderConfig(), ["one", "two", "three"])
        for _ in range(3):
            provider.generate_text([ChatMessage(role="user", content="hi")])
        assert sdk.keys_used == ["one", "two", "three"]

    def test_a_client_is_built_once_per_key_not_once_per_request(
        self, monkeypatch, provider_config
    ):
        """A client holds a connection pool; rebuilding it every call wastes it."""
        monkeypatch.setenv("LUMEN_KEY_ROTATION_STRATEGY", "round_robin")
        provider, sdk = self.build(monkeypatch, ProviderConfig(), ["one", "two"])
        for _ in range(6):
            provider.generate_text([ChatMessage(role="user", content="hi")])
        assert sdk.keys_built == ["one", "two"]

    def test_a_rate_limited_call_retries_under_a_different_key(
        self, monkeypatch, provider_config
    ):
        """The whole point: the retry goes to a meter that is not empty."""
        limit = ProviderRateLimitError("quota", provider="gemini")
        provider, sdk = self.build(
            monkeypatch, provider_config, ["one", "two", "three"], errors=[limit]
        )
        provider.generate_text([ChatMessage(role="user", content="hi")])
        assert len(sdk.keys_used) == 2
        assert sdk.keys_used[0] != sdk.keys_used[1]

    def test_one_key_still_works_exactly_as_before(self, monkeypatch, provider_config):
        provider, sdk = self.build(monkeypatch, provider_config, ["solo"])
        provider.generate_text([ChatMessage(role="user", content="hi")])
        provider.generate_text([ChatMessage(role="user", content="hi")])
        assert sdk.keys_used == ["solo", "solo"]

    def test_a_single_key_still_waits_out_a_rate_limit(self, monkeypatch):
        """With nowhere else to go, the long quota-minute ceiling stands."""
        monkeypatch.setenv("GEMINI_API_KEYS", "solo")
        RecordingSdk().install(monkeypatch)
        config = ProviderConfig(rate_limit_backoff_max_seconds=65.0, backoff_max_seconds=8.0)
        provider = GeminiLLMProvider("gemini-2.5-flash", ModelRole.LIGHTWEIGHT, config)
        assert provider._rate_limit_backoff_max() == 65.0

    def test_several_keys_do_not_sit_out_a_quota_minute(self, monkeypatch):
        """
        Waiting a minute when a fresh key is available would throw away the
        capacity the extra keys were configured for.
        """
        monkeypatch.setenv("GEMINI_API_KEYS", "one,two")
        RecordingSdk().install(monkeypatch)
        config = ProviderConfig(rate_limit_backoff_max_seconds=65.0, backoff_max_seconds=8.0)
        provider = GeminiLLMProvider("gemini-2.5-flash", ModelRole.LIGHTWEIGHT, config)
        assert provider._rate_limit_backoff_max() == 8.0

    def test_embedding_rotates_too(self, monkeypatch, provider_config):
        """Embeddings are the bulk of the calls, so they matter most here."""
        monkeypatch.setenv("GEMINI_API_KEYS", "one,two")
        monkeypatch.setenv("LUMEN_KEY_ROTATION_STRATEGY", "round_robin")
        vectors = SimpleNamespace(embeddings=[SimpleNamespace(values=[0.1] * 768)])
        sdk = RecordingSdk(result=vectors).install(monkeypatch)
        provider = GeminiEmbeddingProvider("text-embedding-004", ProviderConfig())
        provider.embed_text("hello")
        provider.embed_text("hello")
        assert sdk.keys_used == ["one", "two"]

    def test_an_unknown_strategy_is_refused_at_construction(
        self, monkeypatch, provider_config
    ):
        monkeypatch.setenv("GEMINI_API_KEYS", "one,two")
        monkeypatch.setenv("LUMEN_KEY_ROTATION_STRATEGY", "shuffle")
        RecordingSdk().install(monkeypatch)
        with pytest.raises(ProviderConfigurationError, match="LUMEN_KEY_ROTATION_STRATEGY"):
            GeminiLLMProvider("gemini-2.5-flash", ModelRole.LIGHTWEIGHT, ProviderConfig())

    def test_a_supplied_client_is_used_whatever_the_keys_say(
        self, monkeypatch, provider_config
    ):
        """Tests hand in a client; rotation must not build one behind its back."""
        monkeypatch.setenv("GEMINI_API_KEYS", "one,two")
        sdk = RecordingSdk().install(monkeypatch)
        provider, models = build_provider(provider_config, result=reply(text="ok"))
        provider.generate_text([ChatMessage(role="user", content="hi")])
        assert sdk.keys_built == []
        assert len(models.calls) == 1

    def test_the_keys_never_reach_a_log_line(self, monkeypatch, provider_config, caplog):
        monkeypatch.setenv("LUMEN_KEY_ROTATION_STRATEGY", "round_robin")
        with caplog.at_level("DEBUG"):
            provider, _ = self.build(monkeypatch, provider_config, ["secret-one", "secret-two"])
            for _ in range(2):
                provider.generate_text([ChatMessage(role="user", content="hi")])
        assert "secret-one" not in caplog.text
        assert "secret-two" not in caplog.text


class TestClosing:
    def test_closing_is_safe(self, provider_config):
        """Nothing to release, but the method has to exist and not complain."""
        provider, _ = build_provider(provider_config)
        provider.close()
