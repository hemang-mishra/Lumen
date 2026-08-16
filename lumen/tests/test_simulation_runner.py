"""
Tests for feeding the written week through the pipeline.

Mostly about the stand-in that speaks for the models. It refuses rather than
improvises, in three separate ways, and every one of those refusals is what
stops a quietly broken corpus from looking like a working one.
"""

from __future__ import annotations

import pytest

from lumen.simulation.corpus import CORPUS, DAY_1, DAY_2
from lumen.simulation.runner import STEP_MARKERS, CorpusScript, build_models

READING_PROMPT = "Return three things: findings, events, and cause-and-effect. FINDINGS (observations)"
CLEANING_PROMPT = "Below is a journal entry someone typed. Make it readable."


class TestAnsweringForADay:
    def test_it_answers_the_step_being_asked_about(self):
        script = CorpusScript(CORPUS)
        script.begin(DAY_1)

        assert "cafe" in script(READING_PROMPT)

    def test_it_answers_for_whichever_day_is_running(self):
        # Two days ask the same reading prompt with different writing in it,
        # so the day cannot be worked out from the prompt and is announced.
        script = CorpusScript(CORPUS)

        script.begin(DAY_1)
        first = script(READING_PROMPT)
        script.begin(DAY_2)
        second = script(READING_PROMPT)

        assert first != second
        assert "measuring myself" in second

    def test_it_remembers_which_steps_ran_for_a_day(self):
        script = CorpusScript(CORPUS)
        script.begin(DAY_1)

        script(CLEANING_PROMPT)
        script(READING_PROMPT)

        assert script.steps_asked_on(1) == ["normalize_text", "extract_reflection"]
        assert script.steps_asked_on(2) == []


class TestItRefusesRatherThanImprovises:
    def test_a_prompt_before_any_day_has_begun(self):
        script = CorpusScript(CORPUS)

        with pytest.raises(RuntimeError, match="before any day had begun"):
            script(READING_PROMPT)

    def test_a_prompt_belonging_to_no_known_step(self):
        script = CorpusScript(CORPUS)
        script.begin(DAY_1)

        with pytest.raises(KeyError, match="no known pipeline step"):
            script("Something nothing in this pipeline would ever ask.")

    def test_a_step_the_running_day_has_no_answer_for(self):
        # The failure a quietly changed corpus produces: a day reaching a
        # stage it was never written to answer. A stand-in that invented
        # something here would let the week keep passing.
        script = CorpusScript(CORPUS)
        script.begin(DAY_1)

        with pytest.raises(KeyError, match="no reply for"):
            script("A faster model read these items and this is what it said.")


class TestTheStepMarkers:
    def test_each_marker_belongs_to_exactly_one_step(self):
        markers = [marker for _, marker in STEP_MARKERS]

        assert len(markers) == len(set(markers))

    def test_no_marker_appears_inside_another_steps_marker(self):
        # An overlap would route one stage's prompt to another stage's
        # answer, which is how a scripted run ends up testing nothing. The
        # search and decision prompts share a heading, which is exactly the
        # collision this guards against.
        for step, marker in STEP_MARKERS:
            others = [other for other_step, other in STEP_MARKERS if other_step != step]
            assert not any(marker in other for other in others), (
                f"{step}'s marker also appears in another step's marker"
            )


class TestBuildingTheModels:
    def test_both_models_share_one_script(self):
        # So nothing has to know which of the two a given stage happens to
        # use, and so the running day only has to be set once.
        script, light, deep = build_models(CORPUS)

        script.begin(DAY_1)
        assert light.generate_structured is not None
        assert deep.generate_structured is not None
        assert script.current == 1


class TestRunningTheWeek:
    def test_it_returns_one_report_per_day(
        self, graph_store, vector_store, ops_store
    ):
        from lumen.config import AppConfig
        from lumen.simulation import simulate_days

        reports = simulate_days(
            CORPUS[:2],
            graph=graph_store,
            vectors=vector_store,
            ops=ops_store,
            config=AppConfig(),
        )

        assert len(reports) == 2
        assert all(report.job_status == "COMPLETE" for report in reports)

    def test_a_day_arrives_the_way_a_real_entry_would(
        self, graph_store, vector_store, ops_store
    ):
        # Through the waiting room, as a conversation that went quiet —
        # not handed straight to the pipeline.
        from lumen.config import AppConfig
        from lumen.simulation import simulate_days

        reports = simulate_days(
            CORPUS[:1],
            graph=graph_store,
            vectors=vector_store,
            ops=ops_store,
            config=AppConfig(),
        )

        buffer = ops_store.buffers.get_buffer(reports[0].session_id)
        assert buffer is not None
        assert buffer.message_count == 1

    def test_models_can_be_supplied_from_outside(
        self, graph_store, vector_store, ops_store
    ):
        # Which is how the same week is run against real models. Nothing
        # announces the day in that case, because a real model does not need
        # to be told which entry it is reading — the entry is in the prompt.
        from lumen.config import AppConfig
        from lumen.providers.fake import FakeLLMProvider
        from lumen.schemas.enums import ModelRole
        from lumen.simulation import simulate_days

        answers = replies_for_first_day()
        light = FakeLLMProvider(lambda prompt: _answer(prompt, answers))
        deep = FakeLLMProvider(
            lambda prompt: _answer(prompt, answers), role=ModelRole.THINKING
        )

        reports = simulate_days(
            CORPUS[:1],
            graph=graph_store,
            vectors=vector_store,
            ops=ops_store,
            models=(light, deep),
            config=AppConfig(),
        )

        assert len(reports) == 1
        assert reports[0].job_status == "COMPLETE"


def replies_for_first_day() -> dict[str, str]:
    """Day one's answers, as a caller supplying their own models would have."""
    from lumen.simulation.corpus import replies_for

    return replies_for(DAY_1)


def _answer(prompt: str, answers: dict[str, str]) -> str:
    """Pick the answer for whichever step this prompt belongs to."""
    for step, marker in STEP_MARKERS:
        if marker in prompt and step in answers:
            return answers[step]
    raise KeyError(f"nothing to say to: {prompt[:80]!r}")


class TestTheCommand:
    def test_it_fills_a_graph_and_says_what_it_made(
        self, tmp_path, monkeypatch, capsys
    ):
        # Phase three's whole point is being able to look at a real graph,
        # and until this there was no way to get anything into one.
        monkeypatch.setenv("LUMEN_GRAPH_DB_PATH", str(tmp_path / "graph"))
        monkeypatch.setenv("LUMEN_OPS_DB_URL", f"sqlite:///{tmp_path / 'ops.db'}")
        monkeypatch.setenv("LUMEN_VECTOR_LOCATION", ":memory:")

        from lumen.simulation.__main__ import main

        assert main() == 0

        printed = capsys.readouterr().out
        assert "Ran 5 days" in printed
        assert "pat_comparison_spiral" in printed
        assert "BeliefNode" in printed
