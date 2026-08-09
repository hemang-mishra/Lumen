"""
Tests for the provider error types.

The point of these classes is the split between failures worth retrying and
failures that are not, so most of what is checked here is that the split is
expressed as a type. If retryability ever came down to matching words in a
message, a library update could quietly turn a retry into a crash.
"""

from __future__ import annotations

import pytest

from lumen.providers.errors import (
    FakeScriptExhaustedError,
    ProviderConfigurationError,
    ProviderContentBlockedError,
    ProviderError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RetryableProviderError,
)
from lumen.schemas.enums import ModelRole

RETRYABLE = [ProviderTimeoutError, ProviderRateLimitError, ProviderUnavailableError]
NOT_RETRYABLE = [
    ProviderConfigurationError,
    ProviderResponseError,
    ProviderContentBlockedError,
    FakeScriptExhaustedError,
]


class TestRetryableSplit:
    @pytest.mark.parametrize("error_class", RETRYABLE)
    def test_recoverable_failures_are_marked_retryable(self, error_class):
        assert issubclass(error_class, RetryableProviderError)

    @pytest.mark.parametrize("error_class", NOT_RETRYABLE)
    def test_permanent_failures_are_not_marked_retryable(self, error_class):
        assert not issubclass(error_class, RetryableProviderError)

    @pytest.mark.parametrize("error_class", RETRYABLE + NOT_RETRYABLE)
    def test_everything_shares_one_base(self, error_class):
        """So a caller can catch every provider failure with one except."""
        assert issubclass(error_class, ProviderError)

    def test_a_blocked_response_is_a_response_failure(self):
        """It arrived and was refused, rather than never getting there."""
        assert issubclass(ProviderContentBlockedError, ProviderResponseError)


class TestContext:
    def test_the_details_are_kept(self):
        error = ProviderError(
            "something went wrong",
            provider="gemini",
            model="gemini-2.5-flash",
            role=ModelRole.LIGHTWEIGHT,
            attempts=2,
        )
        assert error.context() == {
            "provider": "gemini",
            "model": "gemini-2.5-flash",
            "model_role": "LIGHTWEIGHT",
            "attempts": 2,
            "error_type": "ProviderError",
        }

    def test_a_bare_error_still_produces_context(self):
        assert ProviderError("bare").context()["model_role"] is None

    def test_the_type_name_is_reported(self):
        error = ProviderTimeoutError("too slow")
        assert error.context()["error_type"] == "ProviderTimeoutError"

    def test_the_message_names_the_provider_and_model(self):
        error = ProviderError("boom", provider="ollama", model="phi-3")
        assert "boom" in str(error)
        assert "ollama/phi-3" in str(error)

    def test_a_message_with_no_provider_stays_plain(self):
        assert str(ProviderError("boom")) == "boom"

    def test_a_provider_without_a_model_is_still_named(self):
        assert "[ollama]" in str(ProviderError("boom", provider="ollama"))

    def test_the_underlying_failure_is_kept(self):
        cause = ValueError("original")
        assert ProviderError("wrapped", cause=cause).cause is cause


class TestRateLimit:
    def test_the_wait_the_server_asked_for_is_kept(self):
        assert ProviderRateLimitError("slow down", retry_after_seconds=30.0).retry_after_seconds == 30.0

    def test_a_server_that_said_nothing_leaves_it_unset(self):
        assert ProviderRateLimitError("slow down").retry_after_seconds is None


class TestContentBlocked:
    def test_the_triggered_categories_are_kept(self):
        error = ProviderContentBlockedError(
            "refused",
            blocked_categories=("HARM_CATEGORY_DANGEROUS_CONTENT",),
        )
        assert error.blocked_categories == ("HARM_CATEGORY_DANGEROUS_CONTENT",)

    def test_the_categories_reach_the_log_context(self):
        error = ProviderContentBlockedError("refused", blocked_categories=("A", "B"))
        assert error.context()["blocked_categories"] == ["A", "B"]

    def test_no_categories_is_allowed(self):
        """Some refusals name a reason without naming a category."""
        assert ProviderContentBlockedError("refused").blocked_categories == ()
