"""
Providers that answer from a script instead of from a model.

These ship as part of the application rather than living among the tests,
because the tests are not the only thing that needs them. A pipeline run that
has to be repeatable, a demonstration on a machine with no network, and a test
that wants to check what a stage does with a particular answer all need the same
thing: a provider that gives a known reply.

They are built on the same base classes as the real ones, so they go through the
same retry, timing and logging path. A stand-in that took a different route
through the code would be testing something other than what runs in production.
"""

from __future__ import annotations

import hashlib
import math
import random
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from lumen.config import ProviderConfig
from lumen.providers.base import BaseEmbeddingProvider, BaseLLMProvider, RawResponse
from lumen.providers.errors import FakeScriptExhaustedError
from lumen.providers.results import ChatMessage, LLMUsage
from lumen.schemas.enums import EmbeddingTaskType, ModelRole

# A script is either a list of replies to hand out in order, a lookup from
# something in the prompt to the reply, or a function that builds one.
Script = Sequence[str] | dict[str, str] | Callable[[str], str]


@dataclass
class RecordedCall:
    """One call that was made, kept so a test can check what was asked."""

    operation: str
    prompt: str
    response_model: str | None = None
    system_instruction: str | None = None
    temperature: float | None = None


@dataclass
class _ScriptState:
    """A script and how much of it has been used."""

    script: Script
    position: int = 0


class FakeLLMProvider(BaseLLMProvider):
    """
    A text provider that answers from a script.

    When it runs out of script it raises, rather than making something up. A
    stand-in that invented a plausible answer would let a test pass after the
    prompt it was checking had silently changed, which is the opposite of what a
    test is for.
    """

    provider_name = "fake"

    def __init__(
        self,
        script: Script | None = None,
        *,
        model: str = "fake-model",
        role: ModelRole = ModelRole.LIGHTWEIGHT,
        config: ProviderConfig | None = None,
    ) -> None:
        super().__init__(model, role, config or ProviderConfig())
        self._state = _ScriptState(script if script is not None else [])
        self.calls: list[RecordedCall] = []
        self.closed = False

    def _request_structured(
        self,
        *,
        prompt: str,
        response_model: type[BaseModel],
        system_instruction: str | None,
        temperature: float,
    ) -> Any:
        self.calls.append(
            RecordedCall(
                operation="generate_structured",
                prompt=prompt,
                response_model=response_model.__name__,
                system_instruction=system_instruction,
                temperature=temperature,
            )
        )
        return self._next_reply(prompt)

    def _request_text(
        self,
        *,
        messages: list[ChatMessage],
        system_instruction: str | None,
        temperature: float,
    ) -> Any:
        prompt = "\n".join(f"{m.role}: {m.content}" for m in messages)
        self.calls.append(
            RecordedCall(
                operation="generate_text",
                prompt=prompt,
                system_instruction=system_instruction,
                temperature=temperature,
            )
        )
        return self._next_reply(prompt)

    def _read_response(self, reply: Any) -> RawResponse:
        """The scripted reply is already just text."""
        text = str(reply)
        return RawResponse(
            text=text,
            usage=LLMUsage(
                prompt_tokens=0,
                completion_tokens=len(text.split()),
                total_tokens=len(text.split()),
            ),
            finish_reason="STOP",
        )

    def close(self) -> None:
        """Note that it was closed, so a test can check that it happens."""
        self.closed = True

    def _next_reply(self, prompt: str) -> str:
        """
        Work out what to say next.

        A function is called with the prompt. A lookup returns the entry whose
        key appears in the prompt. A list is worked through in order.
        """
        script = self._state.script

        if callable(script):
            return script(prompt)

        if isinstance(script, dict):
            for key, reply in script.items():
                if key in prompt:
                    return reply
            raise FakeScriptExhaustedError(
                f"no scripted reply matches this prompt. Keys available: "
                f"{sorted(script)}",
                provider=self.provider_name,
                model=self.model_name,
                role=self.model_role,
            )

        if self._state.position >= len(script):
            raise FakeScriptExhaustedError(
                f"the script has {len(script)} replies and all of them have been "
                f"used; this is call number {self._state.position + 1}",
                provider=self.provider_name,
                model=self.model_name,
                role=self.model_role,
            )

        reply = script[self._state.position]
        self._state.position += 1
        return reply


class FakeEmbeddingProvider(BaseEmbeddingProvider):
    """
    An embedding provider that makes vectors out of the text itself.

    The same text always gives the same vector and different text gives a
    different one, which is all a retrieval test actually needs. Because the
    vector comes from a hash rather than a model, it is identical on every
    machine, so a test that depends on which of two things is closer keeps
    giving the same answer.
    """

    provider_name = "fake"

    def __init__(
        self,
        *,
        model: str = "fake-embedding",
        dimensions: int = 768,
        config: ProviderConfig | None = None,
    ) -> None:
        super().__init__(model, config or ProviderConfig(), dimensions)
        self.embedded: list[str] = []
        self.closed = False

    def _embed_chunk(
        self,
        texts: list[str],
        task_type: EmbeddingTaskType,
    ) -> list[list[float]]:
        self.embedded.extend(texts)
        return [self._vector_for(text) for text in texts]

    def close(self) -> None:
        """Note that it was closed, so a test can check that it happens."""
        self.closed = True

    def _vector_for(self, text: str) -> list[float]:
        """
        Build a repeatable vector from a piece of text.

        The text is hashed, the hash seeds a random number generator, and the
        numbers it produces are scaled so the vector has a length of one — the
        same shape a real embedding model produces, which keeps cosine
        similarity meaningful.
        """
        digest = hashlib.blake2b(text.encode("utf-8"), digest_size=16).digest()
        generator = random.Random(int.from_bytes(digest, "big"))
        values = [generator.gauss(0.0, 1.0) for _ in range(self.dimensions)]

        length = math.sqrt(sum(value * value for value in values))
        if length == 0:  # pragma: no cover - effectively impossible
            return [0.0] * self.dimensions
        return [value / length for value in values]


class FakeScriptRegistry:
    """
    Scripts waiting for a fake provider that has not been built yet.

    When the fake is chosen through configuration, whoever builds it is the
    factory, and there is no way to hand a script through an environment
    variable. So a script is left here under a role, and the factory picks it up.

    Guarded by a lock because a test that runs work on several threads would
    otherwise be registering and reading at the same time.
    """

    def __init__(self) -> None:
        self._scripts: dict[ModelRole, Script] = {}
        self._lock = threading.Lock()

    def register(self, role: ModelRole, script: Script) -> None:
        """Leave a script for whatever gets built for this role."""
        with self._lock:
            self._scripts[role] = script

    def get(self, role: ModelRole) -> Script | None:
        """The script left for this role, if there is one."""
        with self._lock:
            return self._scripts.get(role)

    def clear(self) -> None:
        """Forget every script, so one test cannot affect the next."""
        with self._lock:
            self._scripts.clear()


# One shared registry. Tests clear it between cases.
fake_scripts = FakeScriptRegistry()


__all__ = [
    "FakeLLMProvider",
    "FakeEmbeddingProvider",
    "FakeScriptRegistry",
    "RecordedCall",
    "Script",
    "fake_scripts",
]
