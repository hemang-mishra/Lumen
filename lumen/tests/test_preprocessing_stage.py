"""
End-to-end tests for the preprocessing stage.

These run the whole stage against scripted stand-in models, so they check
the thing that actually ships: the order the steps run in, what each is
handed, and how the pieces are assembled at the end.

The worked examples from the specification are here — the code-mixed
sentence and the two filler examples — because those are the cases the
design was argued from, and a change that breaks one of them is a change
to what the system promises.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from lumen.config import AppConfig, PipelineConfig
from lumen.pipeline import preprocess
from lumen.schemas.enums import EntryClass, QualityGateDecision, SourceModality

LONG_ENOUGH = " ".join(f"word{index}" for index in range(40))


def normalize_reply(text: str, **extra) -> str:
    payload = {"cleaned_text": text, "detected_languages": ["en"], "translated": False}
    payload.update(extra)
    return json.dumps(payload)


def structure_reply(*texts: str, coreference=None) -> str:
    return json.dumps(
        {
            "episodes": [
                {"episode_summary": f"topic {index}", "text": text}
                for index, text in enumerate(texts, start=1)
            ],
            "coreference": coreference or {"resolved_entities": [], "ambiguous_refs": []},
        }
    )


def triage_reply(*scores: float) -> str:
    return json.dumps(
        {
            "scores": [
                {"episode_index": index, "coherence_score": score, "reason": "because"}
                for index, score in enumerate(scores, start=1)
            ]
        }
    )


def full_script(text: str = LONG_ENOUGH, *, episodes=None, scores=(0.9,)) -> dict:
    return {
        "normalize_text": normalize_reply(text),
        "structure": structure_reply(*(episodes or [text])),
        "triage": triage_reply(*scores),
    }


class TestCallCost:
    def test_a_typed_entry_costs_three_calls(self, make_event, scripted_providers):
        light, thinking = scripted_providers(full_script())
        preprocess(
            make_event([("USER", LONG_ENOUGH)]), lightweight=light, thinking=thinking
        )

        assert len(light.calls) == 2  # cleaning, then scoring
        assert len(thinking.calls) == 1  # splitting

    def test_a_short_entry_costs_one_call(self, make_event, scripted_providers):
        light, thinking = scripted_providers(
            {
                "normalize_text": normalize_reply("only a few words here"),
                "reflection": json.dumps({"reflection_prompts": ["say more?"]}),
            }
        )
        preprocess(
            make_event([("USER", "only a few words here")]),
            lightweight=light,
            thinking=thinking,
        )

        assert len(light.calls) == 2  # cleaning, then the questions
        assert thinking.calls == []

    def test_a_conversation_costs_one_more(self, make_event, scripted_providers):
        script = full_script()
        script["conversation"] = json.dumps(
            {"turns": [], "session_summary": LONG_ENOUGH}
        )
        light, thinking = scripted_providers(script)
        preprocess(
            make_event([("USER", "rough day"), ("AI", "why?"), ("USER", "no idea")]),
            lightweight=light,
            thinking=thinking,
        )

        assert len(light.calls) == 2
        assert len(thinking.calls) == 2  # untangling, then splitting

    def test_a_monologue_never_asks_about_a_conversation(
        self, make_event, scripted_providers
    ):
        light, thinking = scripted_providers(full_script())
        preprocess(
            make_event([("USER", LONG_ENOUGH)]), lightweight=light, thinking=thinking
        )

        assert all("CONVERSATION:" not in call.prompt for call in thinking.calls)


class TestWorkedExamples:
    def test_a_code_mixed_entry_comes_back_in_english(
        self, make_event, scripted_providers
    ):
        raw = (
            "I had a meeting with Jordan today and mujhe samajh nahi aaya ki "
            "he was being passive-aggressive"
        )
        english = (
            "I had a meeting with Jordan today and I didn't understand whether "
            "he was being passive-aggressive"
        )
        light, thinking = scripted_providers(
            {
                "normalize_text": normalize_reply(
                    english, detected_languages=["en", "hi"], translated=True
                ),
                "structure": structure_reply(english),
                "triage": triage_reply(0.8),
            }
        )
        result = preprocess(
            make_event([("USER", raw)]),
            lightweight=light,
            thinking=thinking,
            config=AppConfig(pipeline=PipelineConfig(min_reflection_words=5)),
        )

        assert result.episodes[0].cleaned_text == english
        # The untranslated text never reaches the splitting step.
        assert "mujhe samajh" not in thinking.calls[0].prompt

    def test_the_untranslated_text_never_reaches_scoring(
        self, make_event, scripted_providers
    ):
        raw = "mujhe samajh nahi aaya " * 10
        english = "I did not understand " * 10
        light, thinking = scripted_providers(
            {
                "normalize_text": normalize_reply(english, translated=True),
                "structure": structure_reply(english),
                "triage": triage_reply(0.8),
            }
        )
        preprocess(make_event([("USER", raw)]), lightweight=light, thinking=thinking)

        scoring_call = light.calls[1]
        assert "mujhe samajh" not in scoring_call.prompt


class TestEpisodeAssembly:
    def test_episodes_are_numbered_consistently(self, make_event, scripted_providers):
        light, thinking = scripted_providers(
            full_script(episodes=["one", "two", "three"], scores=(0.9, 0.8, 0.7))
        )
        result = preprocess(
            make_event([("USER", LONG_ENOUGH)]), lightweight=light, thinking=thinking
        )

        assert [episode.episode_index for episode in result.episodes] == [1, 2, 3]
        assert all(
            episode.total_episodes_in_entry == 3 for episode in result.episodes
        )

    def test_episode_ids_are_dated_and_ordered(self, make_event, scripted_providers):
        light, thinking = scripted_providers(
            full_script(episodes=["one", "two"], scores=(0.9, 0.8))
        )
        result = preprocess(
            make_event([("USER", LONG_ENOUGH)], event_date=date(2026, 6, 11)),
            lightweight=light,
            thinking=thinking,
        )

        assert [episode.episode_id for episode in result.episodes] == [
            "ep_2026_06_11_001",
            "ep_2026_06_11_002",
        ]

    def test_each_episode_carries_its_own_fingerprint(
        self, make_event, scripted_providers
    ):
        light, thinking = scripted_providers(
            full_script(episodes=["one", "two"], scores=(0.9, 0.8))
        )
        result = preprocess(
            make_event([("USER", LONG_ENOUGH)]), lightweight=light, thinking=thinking
        )

        hashes = {episode.raw_text_hash for episode in result.episodes}
        assert len(hashes) == 2

    def test_summaries_and_themes_survive(self, make_event, scripted_providers):
        structure = json.dumps(
            {
                "episodes": [
                    {
                        "episode_summary": "The mentor conflict",
                        "text": "Jordan pushed back.",
                        "overarching_themes": ["Work", "Social Dynamics"],
                        "historical_era": "exam prep",
                    }
                ],
                "coreference": {"resolved_entities": [], "ambiguous_refs": []},
            }
        )
        light, thinking = scripted_providers(
            {
                "normalize_text": normalize_reply(LONG_ENOUGH),
                "structure": structure,
                "triage": triage_reply(0.9),
            }
        )
        result = preprocess(
            make_event([("USER", LONG_ENOUGH)]), lightweight=light, thinking=thinking
        )

        episode = result.episodes[0]
        assert episode.episode_summary == "The mentor conflict"
        assert episode.overarching_themes == ["Work", "Social Dynamics"]
        assert episode.historical_era == "exam prep"

    def test_the_coreference_map_is_keyed_to_the_session(
        self, make_event, scripted_providers
    ):
        coreference = {
            "resolved_entities": [
                {
                    "span": "he",
                    "resolved_to": "Jordan",
                    "confidence": 0.94,
                    "resolution_basis": "most_recent_named_antecedent",
                }
            ],
            "ambiguous_refs": [
                {
                    "span": "she",
                    "candidates": ["Neha", "Priya"],
                    "reason": "two referents close together",
                }
            ],
        }
        light, thinking = scripted_providers(
            {
                "normalize_text": normalize_reply(LONG_ENOUGH),
                "structure": structure_reply(LONG_ENOUGH, coreference=coreference),
                "triage": triage_reply(0.9),
            }
        )
        result = preprocess(
            make_event([("USER", LONG_ENOUGH)], session_id="sess_abc"),
            lightweight=light,
            thinking=thinking,
        )

        assert result.coreference_map.entry_id == "sess_abc"
        assert result.coreference_map.resolved_entities[0].resolved_to == "Jordan"
        assert result.coreference_map.ambiguous_refs[0].candidates == ["Neha", "Priya"]


class TestVoiceHandling:
    def test_a_recording_is_given_the_speech_instructions(
        self, make_event, scripted_providers
    ):
        light, thinking = scripted_providers(
            {
                "normalize_voice": normalize_reply(LONG_ENOUGH),
                "structure": structure_reply(LONG_ENOUGH),
                "triage": triage_reply(0.9),
            }
        )
        preprocess(
            make_event(
                [("USER", LONG_ENOUGH)], source_modality=SourceModality.VOICE_NOTE
            ),
            lightweight=light,
            thinking=thinking,
        )

        assert "TRANSCRIPT:" in light.calls[0].prompt

    def test_typed_input_is_not(self, make_event, scripted_providers):
        light, thinking = scripted_providers(full_script())
        preprocess(
            make_event([("USER", LONG_ENOUGH)]), lightweight=light, thinking=thinking
        )

        assert "TRANSCRIPT:" not in light.calls[0].prompt


class TestConversationHandling:
    def test_only_settled_conclusions_move_forward(self, make_event, scripted_providers):
        settled = "I was avoiding the tradeoff rather than making it. " * 6
        script = full_script(settled)
        script["conversation"] = json.dumps(
            {
                "turns": [
                    {
                        "message_id": "m0",
                        "dialogue_act": "EXPRESSIVE",
                        "co_created_marker": False,
                    }
                ],
                "session_summary": settled,
            }
        )
        light, thinking = scripted_providers(script)
        result = preprocess(
            make_event(
                [
                    ("USER", "maybe I am just bad at this"),
                    ("AI", "what makes you say that?"),
                    ("USER", "actually no, I was avoiding the tradeoff"),
                ]
            ),
            lightweight=light,
            thinking=thinking,
        )

        # The discarded theory does not survive into the episodes.
        assert "just bad at this" not in result.episodes[0].cleaned_text

    def test_the_assistants_words_never_reach_an_episode(
        self, make_event, scripted_providers
    ):
        script = full_script()
        script["conversation"] = json.dumps(
            {"turns": [], "session_summary": LONG_ENOUGH}
        )
        light, thinking = scripted_providers(script)
        result = preprocess(
            make_event(
                [
                    ("USER", "rough day"),
                    ("AI", "have you considered that you are catastrophising"),
                    ("USER", "no idea"),
                ]
            ),
            lightweight=light,
            thinking=thinking,
        )

        assert all(
            "catastrophising" not in episode.cleaned_text for episode in result.episodes
        )

    def test_wording_the_person_adopted_reaches_the_result(
        self, make_event, scripted_providers
    ):
        # Carried at the level of the whole entry, like the reference map,
        # because topics are not split until several steps later and a
        # phrase cannot be tied to one of them before they exist.
        script = full_script()
        script["conversation"] = json.dumps(
            {
                "turns": [],
                "session_summary": LONG_ENOUGH,
                "co_created_spans": ["the gym is a forcing function"],
            }
        )
        light, thinking = scripted_providers(script)

        result = preprocess(
            make_event([("USER", "rough day"), ("AI", "a forcing function?")]),
            lightweight=light,
            thinking=thinking,
        )

        assert result.co_created_spans == ["the gym is a forcing function"]

    def test_a_monologue_has_no_adopted_wording(self, make_event, scripted_providers):
        light, thinking = scripted_providers(full_script())

        result = preprocess(
            make_event([("USER", LONG_ENOUGH)]), lightweight=light, thinking=thinking
        )

        assert result.co_created_spans == []


class TestResultShape:
    def test_the_result_reports_the_session_it_came_from(
        self, make_event, scripted_providers
    ):
        light, thinking = scripted_providers(full_script())
        result = preprocess(
            make_event([("USER", LONG_ENOUGH)], session_id="sess_xyz"),
            lightweight=light,
            thinking=thinking,
        )

        assert result.session_id == "sess_xyz"

    def test_processing_time_is_recorded(self, make_event, scripted_providers):
        light, thinking = scripted_providers(full_script())
        result = preprocess(
            make_event([("USER", LONG_ENOUGH)]), lightweight=light, thinking=thinking
        )

        assert result.processing_time_ms >= 0

    def test_it_runs_without_being_handed_a_config(
        self, make_event, scripted_providers
    ):
        light, thinking = scripted_providers(full_script())
        result = preprocess(
            make_event([("USER", LONG_ENOUGH)]), lightweight=light, thinking=thinking
        )

        assert result.quality_gate_decision == QualityGateDecision.REFLECTION


class TestDegradedRuns:
    def test_a_session_survives_every_step_failing(self, make_event, scripted_providers):
        # No script at all, so every call raises. Nothing should be lost.
        light, thinking = scripted_providers({})
        result = preprocess(
            make_event([("USER", LONG_ENOUGH)]), lightweight=light, thinking=thinking
        )

        assert len(result.episodes) == 1
        assert result.episodes[0].cleaned_text == LONG_ENOUGH
        assert result.episodes[0].entry_class == EntryClass.RAW_CAPTURE
        assert result.quality_gate_decision == QualityGateDecision.RAW_CAPTURE

    def test_a_failed_split_still_produces_a_usable_episode(
        self, make_event, scripted_providers
    ):
        light, thinking = scripted_providers(
            {
                "normalize_text": normalize_reply(LONG_ENOUGH),
                "triage": triage_reply(0.9),
            }
        )
        result = preprocess(
            make_event([("USER", LONG_ENOUGH)]), lightweight=light, thinking=thinking
        )

        assert len(result.episodes) == 1
        assert result.episodes[0].episode_summary
        assert result.episodes[0].entry_class == EntryClass.REFLECTION

    def test_a_failed_scoring_never_promotes_an_entry(
        self, make_event, scripted_providers
    ):
        light, thinking = scripted_providers(
            {
                "normalize_text": normalize_reply(LONG_ENOUGH),
                "structure": structure_reply(LONG_ENOUGH),
            }
        )
        result = preprocess(
            make_event([("USER", LONG_ENOUGH)]), lightweight=light, thinking=thinking
        )

        assert result.episodes[0].entry_class == EntryClass.RAW_CAPTURE
        assert result.quality_gate_decision == QualityGateDecision.RAW_CAPTURE


class TestNoDatabaseDependency:
    def test_the_pipeline_package_never_reaches_for_the_operational_store(self):
        # A stage that could read settings from the database would be a way
        # to change its behaviour at runtime, which is exactly what the pure
        # function rule exists to prevent.
        from pathlib import Path

        package = Path(__file__).resolve().parents[1] / "pipeline"
        offenders = [
            path.name
            for path in package.rglob("*.py")
            if "lumen.operational" in path.read_text()
        ]
        assert offenders == []

    def test_the_stage_needs_nothing_installed(self, make_event, scripted_providers):
        # No database, no network, no credentials — just two stand-in models.
        light, thinking = scripted_providers(full_script())
        result = preprocess(
            make_event([("USER", LONG_ENOUGH)]), lightweight=light, thinking=thinking
        )

        assert result.episodes


@pytest.mark.parametrize("modality", [SourceModality.VOICE_NOTE, SourceModality.TEXT_ENTRY])
class TestBothModalities:
    def test_a_full_run_works_either_way(
        self, make_event, scripted_providers, modality
    ):
        light, thinking = scripted_providers(
            {
                "normalize_voice": normalize_reply(LONG_ENOUGH),
                "normalize_text": normalize_reply(LONG_ENOUGH),
                "structure": structure_reply(LONG_ENOUGH),
                "triage": triage_reply(0.9),
            }
        )
        result = preprocess(
            make_event([("USER", LONG_ENOUGH)], source_modality=modality),
            lightweight=light,
            thinking=thinking,
        )

        assert result.quality_gate_decision == QualityGateDecision.REFLECTION
