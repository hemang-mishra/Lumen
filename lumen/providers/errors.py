"""
The errors a provider can raise.

Every vendor SDK has its own exception types. Each provider translates them
into the ones here, so callers can react to what went wrong without knowing
which vendor produced it.

The split that matters is retryable versus not. A dropped connection is worth
trying again; a rejected request is not, and retrying it just wastes time and
quota. That difference is expressed as a class, never as a check on the text of
a message, because messages change between library versions and classes do not.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lumen.schemas.enums import ModelRole


class ProviderError(Exception):
    """
    Base class for anything that goes wrong talking to a model.

    Carries the details worth having in a log line: which provider and model
    were involved, which role asked for them, and how many attempts were made.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        model: str | None = None,
        role: "ModelRole | None" = None,
        attempts: int = 1,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.model = model
        self.role = role
        self.attempts = attempts
        self.cause = cause

    def context(self) -> dict[str, Any]:
        """The details of this failure, shaped for a log line."""
        return {
            "provider": self.provider,
            "model": self.model,
            "model_role": self.role.value if self.role is not None else None,
            "attempts": self.attempts,
            "error_type": type(self).__name__,
        }

    def __str__(self) -> str:
        parts = [self.message]
        if self.provider:
            where = self.provider
            if self.model:
                where = f"{where}/{self.model}"
            parts.append(f"[{where}]")
        return " ".join(parts)


class RetryableProviderError(ProviderError):
    """
    A failure that might not happen again.

    The call never reached the model, or reached it and was turned away for a
    reason that has nothing to do with what was asked. Waiting and trying
    again is reasonable.
    """


class ProviderTimeoutError(RetryableProviderError):
    """The model took longer than the time budget allowed."""


class ProviderRateLimitError(RetryableProviderError):
    """
    Too many requests were sent, so this one was refused.

    When the server says how long to wait, that instruction is followed
    instead of the usual backoff, because it knows when the quota resets and
    we are only guessing.
    """

    def __init__(self, message: str, *, retry_after_seconds: float | None = None, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.retry_after_seconds = retry_after_seconds


class ProviderUnavailableError(RetryableProviderError):
    """
    The model could not be reached, or the server said it was having trouble.

    Covers a refused connection (a local daemon that is not running) as well
    as server-side errors.
    """


class ProviderConfigurationError(ProviderError):
    """
    Something about the setup is wrong, and no amount of retrying fixes it.

    A missing credential, an unknown provider name, a role asked to do a job
    it cannot do, or an embedding model whose vector width we do not know.
    Raised as early as possible, ideally while the process is still starting.
    """


class ProviderResponseError(ProviderError):
    """
    The model answered, but the answer is not usable.

    A rejected request, an empty response, or output cut short by a token
    limit. Retrying the same request would produce the same outcome.
    """


class ProviderContentBlockedError(ProviderResponseError):
    """
    The provider's safety filters refused the content.

    This matters more here than in most applications. Journal entries
    legitimately discuss self-harm, violence and conflict, and a safety filter
    cannot tell the difference between describing a feeling and encouraging an
    act. Those entries are exactly the ones worth keeping.

    It is deliberately its own type so callers can send the entry to a human
    instead of retrying, which would fail the same way every time.
    """

    def __init__(self, message: str, *, blocked_categories: tuple[str, ...] = (), **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.blocked_categories = blocked_categories

    def context(self) -> dict[str, Any]:
        details = super().context()
        details["blocked_categories"] = list(self.blocked_categories)
        return details


class FakeScriptExhaustedError(ProviderError):
    """
    A scripted test provider was asked for a response it does not have.

    Raised loudly on purpose. If the fake invented something plausible
    instead, a test whose prompt had silently changed would still pass.
    """


__all__ = [
    "ProviderError",
    "RetryableProviderError",
    "ProviderTimeoutError",
    "ProviderRateLimitError",
    "ProviderUnavailableError",
    "ProviderConfigurationError",
    "ProviderResponseError",
    "ProviderContentBlockedError",
    "FakeScriptExhaustedError",
]
