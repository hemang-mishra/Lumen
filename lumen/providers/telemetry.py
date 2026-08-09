"""
One log line per model call.

Enough is recorded to answer the questions that come up later — which model
ran, how long it took, how many tokens it cost, whether it had to retry — while
leaving out the thing that would be most tempting to record.

Prompts are journal entries. Writing them here would turn the log file into a
second copy of somebody's private history, sitting in plain text next to the
database that is supposed to hold it. So the bodies are left out unless
somebody deliberately turns them on to debug something.

The trace id is not passed in. It is picked up from the surrounding run, which
is also why anything running on a separate thread has to carry that context
across itself.
"""

from __future__ import annotations

import logging
from typing import Any

from lumen.providers.results import LLMUsage
from lumen.schemas.enums import ModelRole

logger = logging.getLogger("lumen.providers")

# Long enough to see what was asked, short enough not to bloat the log file.
_MAX_BODY_CHARS = 2000


def log_llm_call(
    *,
    provider: str,
    model: str,
    role: ModelRole,
    operation: str,
    outcome: str,
    latency_ms: int,
    elapsed_ms: int,
    attempts: int = 1,
    usage: LLMUsage | None = None,
    finish_reason: str | None = None,
    error_type: str | None = None,
    prompt: str | None = None,
    completion: str | None = None,
    log_prompts: bool = False,
    extra: dict[str, Any] | None = None,
) -> None:
    """
    Record that a model call happened.

    Successes are logged at info level and failures at error level, so a
    problem stands out without needing to be searched for.
    """
    fields: dict[str, Any] = {
        "provider": provider,
        "model": model,
        "model_role": role.value,
        "operation": operation,
        "outcome": outcome,
        "latency_ms": latency_ms,
        "elapsed_ms": elapsed_ms,
        "attempts": attempts,
    }

    if usage is not None:
        fields["prompt_tokens"] = usage.prompt_tokens
        fields["completion_tokens"] = usage.completion_tokens
        fields["total_tokens"] = usage.total_tokens

    if finish_reason is not None:
        fields["finish_reason"] = finish_reason
    if error_type is not None:
        fields["error_type"] = error_type
    if extra:
        fields.update(extra)

    if log_prompts:
        if prompt is not None:
            fields["prompt"] = _truncate(prompt)
        if completion is not None:
            fields["completion"] = _truncate(completion)

    level = logging.ERROR if outcome == "FAILED" else logging.INFO
    logger.log(level, "model call %s %s", operation, outcome.lower(), extra=fields)


def log_embedding_call(
    *,
    provider: str,
    model: str,
    operation: str,
    outcome: str,
    elapsed_ms: int,
    text_count: int,
    task_type: str,
    error_type: str | None = None,
) -> None:
    """
    Record that an embedding call happened.

    Kept separate from the text-generation version because the interesting
    numbers are different: how many texts went in and what they were labelled
    as, rather than tokens and finish reasons.
    """
    fields: dict[str, Any] = {
        "provider": provider,
        "model": model,
        "model_role": ModelRole.EMBEDDING.value,
        "operation": operation,
        "outcome": outcome,
        "elapsed_ms": elapsed_ms,
        "text_count": text_count,
        "task_type": task_type,
    }
    if error_type is not None:
        fields["error_type"] = error_type

    level = logging.ERROR if outcome == "FAILED" else logging.INFO
    logger.log(level, "embedding call %s %s", operation, outcome.lower(), extra=fields)


def _truncate(body: str) -> str:
    """Shorten a long body, saying how much was left out."""
    if len(body) <= _MAX_BODY_CHARS:
        return body
    dropped = len(body) - _MAX_BODY_CHARS
    return f"{body[:_MAX_BODY_CHARS]}... [{dropped} more characters]"


__all__ = ["log_llm_call", "log_embedding_call"]
