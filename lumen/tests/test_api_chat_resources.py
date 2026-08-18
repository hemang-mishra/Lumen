"""
Opening the models a conversation needs, and only when one is held.

Built late on purpose. Talking needs a model; every other route in this
service reads two local databases and needs none. A deployment with nothing
configured still starts, still serves the graph, and refuses only this.

The voice is separate again inside that, because speech is the one job with
no local option — so a deployment can perfectly well have a chat model and no
voice, and that has to be an ordinary state rather than a failure.
"""

from __future__ import annotations

import pytest

from lumen.api.resources import LazyChatStack
from lumen.config import AppConfig, ChatConfig, ProviderConfig
from lumen.providers.errors import ProviderConfigurationError
from lumen.query.chat import ChatEngine


class HeldSearch:
    """Stands in for the search stack, which is opened elsewhere."""

    def __init__(self):
        self.asked = 0

    def get(self):
        self.asked += 1
        return object()


def settings(**providers) -> AppConfig:
    """Configuration with everything pointed at the stand-ins by default."""
    chosen = {
        "lightweight_provider": "fake",
        "thinking_provider": "fake",
        "conversation_provider": "fake",
        "transcription_provider": "fake",
        "tts_provider": "fake",
        "embedding_provider": "fake",
        "embedding_model": "fake-embedding",
    }
    voice = providers.pop("voice_enabled", False)
    chosen.update(providers)
    return AppConfig(
        providers=ProviderConfig(**chosen), chat=ChatConfig(voice_enabled=voice)
    )


def a_stack(config: AppConfig | None = None, search=None) -> LazyChatStack:
    """A chat stack with everything else stubbed."""
    return LazyChatStack(
        config=config or settings(),
        search=search or HeldSearch(),
        formulator=object(),
        composer=object(),
        memory=object(),
        sessions=object(),
    )


class TestBuildingItLate:
    def test_nothing_is_opened_until_somebody_talks(self):
        search = HeldSearch()

        a_stack(search=search)

        assert search.asked == 0

    def test_the_engine_is_built_on_the_first_turn(self):
        assert isinstance(a_stack().engine(), ChatEngine)

    def test_the_same_engine_comes_back_every_time(self):
        stack = a_stack()

        assert stack.engine() is stack.engine()

    def test_the_search_is_borrowed_rather_than_opened_again(self):
        search = HeldSearch()
        stack = a_stack(search=search)

        stack.engine()
        stack.engine()

        assert search.asked == 1

    def test_a_missing_conversation_model_is_a_refusal_not_a_silence(self):
        stack = a_stack(settings(conversation_provider="nonesuch"))

        with pytest.raises(ProviderConfigurationError):
            stack.engine()


class TestTheVoice:
    def test_it_is_left_alone_unless_it_was_switched_on(self):
        stack = a_stack(settings(voice_enabled=False))

        assert stack.engine()._speech is None

    def test_it_is_opened_when_it_was_switched_on(self):
        stack = a_stack(settings(voice_enabled=True))

        assert stack.engine()._speech is not None

    def test_a_missing_voice_does_not_stop_anybody_talking(self):
        """
        It costs the spoken half of a conversation and nothing else, so it is
        set aside rather than refused.
        """
        stack = a_stack(settings(voice_enabled=True, tts_provider="nonesuch"))

        engine = stack.engine()

        assert isinstance(engine, ChatEngine)
        assert engine._speech is None


class TestListening:
    def test_the_ear_is_built_on_its_own(self):
        stack = a_stack()

        assert stack.listener().provider_name == "fake"

    def test_the_same_one_comes_back_every_time(self):
        stack = a_stack()

        assert stack.listener() is stack.listener()

    def test_a_deployment_that_cannot_listen_says_so(self):
        stack = a_stack(settings(transcription_provider="nonesuch"))

        with pytest.raises(ProviderConfigurationError):
            stack.listener()


class TestClosingIt:
    def test_what_it_opened_is_released(self):
        stack = a_stack(settings(voice_enabled=True))
        stack.engine()
        stack.listener()

        stack.close()

        assert stack._engine is None
        assert stack._llm is None

    def test_closing_twice_is_harmless(self):
        stack = a_stack()
        stack.engine()

        stack.close()
        stack.close()

    def test_one_provider_refusing_to_close_does_not_stop_the_rest(self):
        stack = a_stack()
        stack.engine()

        class Stubborn:
            def close(self):
                raise RuntimeError("will not close")

        stack._llm = Stubborn()
        stack._listener = Stubborn()

        stack.close()

        assert stack._llm is None
