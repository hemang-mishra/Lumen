"""
Talking to Lumen from a terminal.

This is the surface the reply quality is actually judged on. A test can prove
the right records reached the model; whether the answer is any good is a
judgement made by reading one, and this is what somebody reads.

So what is checked here is that it wires up correctly, prints both halves,
and closes everything it opened.
"""

from __future__ import annotations

import contextlib
import io
import json

import pytest

from lumen.chat.session import USER, ChatRunner, build_runner, converse
from lumen.config import (
    AppConfig,
    ChatConfig,
    GraphConfig,
    OperationalConfig,
    ProviderConfig,
    VectorConfig,
)
from lumen.providers.fake import FakeLLMProvider
from lumen.query.chat import ReplyDone, TurnAccepted
from lumen.schemas.enums import ModelRole

REPLY = "That is a real change. What made today different?"


def a_reading() -> str:
    return json.dumps(
        {
            "triggers": [],
            "emotional_register": "REFLECTIVE",
            "named_entities": [],
            "confidence": 0.8,
            "critical_domain_opened": None,
        }
    )


@pytest.fixture
def offline(tmp_path):
    """Settings with every model pointed at a stand-in and nothing on a network."""
    return AppConfig(
        graph=GraphConfig(db_root=str(tmp_path / "graph.db")),
        operational=OperationalConfig(db_url=f"sqlite:///{tmp_path}/ops.db"),
        vector=VectorConfig(location=":memory:"),
        chat=ChatConfig(),
        providers=ProviderConfig(
            lightweight_provider="fake",
            thinking_provider="fake",
            conversation_provider="fake",
            embedding_provider="fake",
            embedding_model="fake-embedding",
            max_attempts=1,
        ),
    )


@pytest.fixture
def runner(offline):
    """A wired-up conversation with scripted models."""
    with build_runner(offline) as built:
        built.engine._llm = FakeLLMProvider(
            [REPLY] * 10, role=ModelRole.CONVERSATION
        )
        built.engine._formulator._llm = FakeLLMProvider([a_reading()] * 10)
        yield built


class TestWiringItUp:
    def test_it_builds_a_working_conversation(self, runner):
        events = list(runner.say("I went for a walk on my own today"))

        assert any(isinstance(event, ReplyDone) for event in events)

    def test_everything_it_opened_is_closed(self, offline):
        closed = []
        with build_runner(offline) as built:
            built._closers = tuple(
                lambda name=index: closed.append(name)
                for index in range(len(built._closers))
            )
        assert closed

    def test_closing_survives_something_refusing_to_close(self):
        """
        A failed cleanup must not mask whatever was really wrong. The rest of
        the stores still get closed.
        """
        closed = []

        def broken():
            raise RuntimeError("will not close")

        ChatRunner(
            engine=None, _closers=(lambda: closed.append("first"), broken)
        ).close()

        assert closed == ["first"]


class TestWhatItPrints:
    def test_the_reply_is_printed_as_it_arrives(self, runner):
        out = io.StringIO()

        with contextlib.redirect_stdout(out):
            converse(runner, ["I went for a walk on my own today"])

        assert REPLY in out.getvalue()

    def test_what_was_behind_the_reply_is_shown_underneath(self, runner):
        """
        The layer is invisible by design, which is right for the product and
        unworkable for judging it. So both halves are printed: the words
        first, the machinery under them.
        """
        out = io.StringIO()

        with contextlib.redirect_stdout(out):
            converse(runner, ["I went for a walk on my own today"])

        printed = out.getvalue()
        assert "reflective" in printed
        assert "from your history" in printed

    def test_what_the_person_said_is_echoed_when_it_is_not_being_typed(self, runner):
        out = io.StringIO()

        with contextlib.redirect_stdout(out):
            converse(runner, ["I went for a walk"], echo=True)

        assert "you: I went for a walk" in out.getvalue()

    def test_it_is_not_echoed_when_somebody_is_typing(self, runner):
        # They have just watched themselves type it.
        out = io.StringIO()

        with contextlib.redirect_stdout(out):
            converse(runner, ["I went for a walk"], echo=False)

        assert "you: I went for a walk" not in out.getvalue()

    def test_blank_lines_are_skipped(self, runner):
        out = io.StringIO()

        with contextlib.redirect_stdout(out):
            converse(runner, ["   ", ""])

        assert out.getvalue() == ""

    def test_a_failed_turn_says_so_rather_than_printing_nothing(self, runner):
        from lumen.providers.errors import ProviderError

        class Refuses(FakeLLMProvider):
            def _request_stream(self, **kwargs):
                raise ProviderError("no model today")

        runner.engine._llm = Refuses([], role=ModelRole.CONVERSATION)
        out = io.StringIO()

        with contextlib.redirect_stdout(out):
            converse(runner, ["I went for a walk"])

        assert "reply_failed" in out.getvalue()

    def test_it_can_be_run_with_nothing_printed(self, runner):
        out = io.StringIO()

        with contextlib.redirect_stdout(out):
            converse(runner, ["I went for a walk"], show=False)

        assert out.getvalue() == ""


class TestWhoIsTalking:
    def test_every_turn_belongs_to_the_same_person(self, runner):
        first = list(runner.say("one thing"))
        second = list(runner.say("another thing"))

        sessions = {
            event.session_id
            for events in (first, second)
            for event in events
            if isinstance(event, TurnAccepted)
        }
        assert len(sessions) == 1
        assert USER


class TestTheCommandItself:
    """
    `python -m lumen.chat`. Thin on purpose — it reads lines, hands them to
    the same runner everything else uses, and prints.
    """

    def test_it_stops_on_an_empty_line(self, monkeypatch, offline):
        import lumen.chat.__main__ as entry

        typed = iter(["I went for a walk", ""])
        monkeypatch.setattr("builtins.input", lambda _="": next(typed))
        monkeypatch.setattr(entry, "AppConfig", lambda: offline)
        monkeypatch.setattr(entry, "load_env", lambda: None)
        monkeypatch.setattr(entry, "configure_logging", lambda _: None)

        said = []
        monkeypatch.setattr(entry, "converse", lambda runner, lines, **kw: said.extend(lines))

        assert entry.main() == 0
        assert said == ["I went for a walk"]

    def test_it_stops_when_the_input_ends(self, monkeypatch, offline):
        import lumen.chat.__main__ as entry

        def ends(_=""):
            raise EOFError

        monkeypatch.setattr("builtins.input", ends)
        monkeypatch.setattr(entry, "AppConfig", lambda: offline)
        monkeypatch.setattr(entry, "load_env", lambda: None)
        monkeypatch.setattr(entry, "configure_logging", lambda _: None)

        said = []
        monkeypatch.setattr(entry, "converse", lambda runner, lines, **kw: said.extend(lines))

        assert entry.main() == 0
        assert said == []


class TestWhatElseIsShown:
    def test_records_held_back_are_counted(self, runner, monkeypatch):
        from lumen.query.chat.contracts import ContextReady

        original = runner.engine.say

        def with_something_withheld(*args, **kwargs):
            for event in original(*args, **kwargs):
                if isinstance(event, ContextReady):
                    yield event.model_copy(update={"withheld": ("bel_1",)})
                else:
                    yield event

        monkeypatch.setattr(runner.engine, "say", with_something_withheld)
        out = io.StringIO()

        with contextlib.redirect_stdout(out):
            converse(runner, ["I went for a walk"])

        assert "held back until you raise it" in out.getvalue()

    def test_an_unreachable_history_is_said_out_loud(self, runner, monkeypatch):
        from lumen.query.chat.contracts import ContextReady

        original = runner.engine.say

        def with_a_failed_search(*args, **kwargs):
            for event in original(*args, **kwargs):
                if isinstance(event, ContextReady):
                    yield event.model_copy(update={"search_failed": True})
                else:
                    yield event

        monkeypatch.setattr(runner.engine, "say", with_a_failed_search)
        out = io.StringIO()

        with contextlib.redirect_stdout(out):
            converse(runner, ["I went for a walk"])

        assert "could not be reached" in out.getvalue()

    def test_a_missing_optional_model_is_shrugged_off(self, offline):
        """
        A missing summariser costs a long chat some coherence; a missing
        voice costs the spoken half. Neither is a reason to refuse to talk.
        """
        from lumen.chat.session import _quiet
        from lumen.providers.errors import ProviderConfigurationError

        def missing():
            raise ProviderConfigurationError("not configured")

        assert _quiet(missing) is None
