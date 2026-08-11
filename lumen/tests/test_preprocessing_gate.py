"""
Tests for the decisions about how much attention an entry earns.

Three separate judgements are checked here, and it is worth being clear that
they are separate. Whether an entry is thrown away is decided by counting.
Whether one topic is read closely is decided by its own score. Whether the
whole session counts as a reflection is decided by whether any single part
of it did.

Both thresholds are tested from either side, because an off-by-one in either
direction quietly changes which of somebody's entries get taken seriously.
"""

from __future__ import annotations

import json

from lumen.config import AppConfig, PipelineConfig
from lumen.pipeline import preprocess
from lumen.schemas.enums import EntryClass, QualityGateDecision

LONG_ENOUGH = " ".join(f"word{index}" for index in range(40))


def config_with(**overrides) -> AppConfig:
    """An application config with only the pipeline settings changed."""
    return AppConfig(pipeline=PipelineConfig(**overrides))


def normalize_reply(text: str) -> str:
    return json.dumps(
        {"cleaned_text": text, "detected_languages": ["en"], "translated": False}
    )


def structure_reply(*texts: str) -> str:
    return json.dumps(
        {
            "episodes": [
                {"episode_summary": f"topic {index}", "text": text}
                for index, text in enumerate(texts, start=1)
            ],
            "coreference": {"resolved_entities": [], "ambiguous_refs": []},
        }
    )


def triage_reply(*scores: float) -> str:
    return json.dumps(
        {
            "scores": [
                {
                    "episode_index": index,
                    "coherence_score": score,
                    "reason": "because",
                    "reflection_prompts": ["a question"] if score < 0.4 else [],
                }
                for index, score in enumerate(scores, start=1)
            ]
        }
    )


class TestDiscard:
    def test_a_buffer_of_only_requests_is_discarded(
        self, make_event, scripted_providers
    ):
        light, thinking = scripted_providers(
            {
                "conversation": json.dumps(
                    {
                        "turns": [
                            {
                                "message_id": "m0",
                                "dialogue_act": "OPERATIONAL_REQUEST",
                                "co_created_marker": False,
                            }
                        ],
                        "session_summary": "",
                    }
                ),
                "normalize_text": normalize_reply(""),
            }
        )
        event = make_event([("USER", "what did I say yesterday?"), ("AI", "you said…")])
        result = preprocess(event, lightweight=light, thinking=thinking)

        assert result.quality_gate_decision == QualityGateDecision.DISCARD
        assert result.episodes == []
        assert result.pending_reflections == []

    def test_a_recording_of_pure_hesitation_is_discarded(
        self, make_event, scripted_providers
    ):
        from lumen.schemas.enums import SourceModality

        light, thinking = scripted_providers({"normalize_voice": normalize_reply("")})
        result = preprocess(
            make_event([("USER", "um uh hmm")], source_modality=SourceModality.VOICE_NOTE),
            lightweight=light,
            thinking=thinking,
        )

        assert result.quality_gate_decision == QualityGateDecision.DISCARD

    def test_the_same_sounds_typed_on_purpose_are_kept(
        self, make_event, scripted_providers
    ):
        # Typed text is never stripped, so this is three words the person
        # chose rather than noise, and it survives as a light capture.
        light, thinking = scripted_providers(
            {
                "normalize_text": normalize_reply("um uh hmm"),
                "reflection": json.dumps({"reflection_prompts": []}),
            }
        )
        result = preprocess(
            make_event([("USER", "um uh hmm")]), lightweight=light, thinking=thinking
        )

        assert result.quality_gate_decision == QualityGateDecision.RAW_CAPTURE

    def test_an_empty_buffer_is_discarded(self, make_event, scripted_providers):
        light, thinking = scripted_providers({"normalize_text": normalize_reply("")})
        result = preprocess(make_event([]), lightweight=light, thinking=thinking)

        assert result.quality_gate_decision == QualityGateDecision.DISCARD

    def test_a_low_quality_entry_is_kept_not_discarded(
        self, make_event, scripted_providers
    ):
        # No score, however low, may discard. That is the whole point of the
        # rule being arithmetic rather than a judgement.
        light, thinking = scripted_providers(
            {
                "normalize_text": normalize_reply(LONG_ENOUGH),
                "structure": structure_reply(LONG_ENOUGH),
                "triage": triage_reply(0.0),
            }
        )
        result = preprocess(
            make_event([("USER", LONG_ENOUGH)]), lightweight=light, thinking=thinking
        )

        assert result.quality_gate_decision == QualityGateDecision.RAW_CAPTURE
        assert len(result.episodes) == 1


class TestWordCountGate:
    def test_one_word_short_of_the_threshold_skips_the_deep_work(
        self, make_event, scripted_providers
    ):
        text = " ".join(["word"] * 29)
        light, thinking = scripted_providers(
            {
                "normalize_text": normalize_reply(text),
                "reflection": json.dumps({"reflection_prompts": ["say more?"]}),
            }
        )
        result = preprocess(
            make_event([("USER", text)]),
            lightweight=light,
            thinking=thinking,
            config=config_with(min_reflection_words=30),
        )

        assert result.quality_gate_decision == QualityGateDecision.RAW_CAPTURE
        assert len(result.episodes) == 1
        assert result.episodes[0].coherence_score == 0.0
        assert result.pending_reflections == ["say more?"]
        # Nothing was asked of the reasoning model.
        assert thinking.calls == []

    def test_exactly_the_threshold_goes_through_the_full_path(
        self, make_event, scripted_providers
    ):
        text = " ".join(["word"] * 30)
        light, thinking = scripted_providers(
            {
                "normalize_text": normalize_reply(text),
                "structure": structure_reply(text),
                "triage": triage_reply(0.9),
            }
        )
        result = preprocess(
            make_event([("USER", text)]),
            lightweight=light,
            thinking=thinking,
            config=config_with(min_reflection_words=30),
        )

        assert result.quality_gate_decision == QualityGateDecision.REFLECTION
        assert len(thinking.calls) == 1

    def test_the_threshold_is_measured_after_cleaning(
        self, make_event, scripted_providers
    ):
        # Forty words of hesitant speech, twenty of them real.
        raw = " ".join(["word", "um"] * 20)
        cleaned = " ".join(["word"] * 20)
        light, thinking = scripted_providers(
            {
                "normalize_voice": normalize_reply(cleaned),
                "reflection": json.dumps({"reflection_prompts": []}),
            }
        )
        from lumen.schemas.enums import SourceModality

        result = preprocess(
            make_event([("USER", raw)], source_modality=SourceModality.VOICE_NOTE),
            lightweight=light,
            thinking=thinking,
            config=config_with(min_reflection_words=30),
        )

        assert result.quality_gate_decision == QualityGateDecision.RAW_CAPTURE


class TestCoherenceThreshold:
    def test_just_below_the_threshold_is_a_light_capture(
        self, make_event, scripted_providers
    ):
        light, thinking = scripted_providers(
            {
                "normalize_text": normalize_reply(LONG_ENOUGH),
                "structure": structure_reply(LONG_ENOUGH),
                "triage": triage_reply(0.39),
            }
        )
        result = preprocess(
            make_event([("USER", LONG_ENOUGH)]),
            lightweight=light,
            thinking=thinking,
            config=config_with(coherence_threshold=0.4),
        )

        assert result.episodes[0].entry_class == EntryClass.RAW_CAPTURE
        assert result.quality_gate_decision == QualityGateDecision.RAW_CAPTURE

    def test_exactly_the_threshold_counts_as_a_reflection(
        self, make_event, scripted_providers
    ):
        light, thinking = scripted_providers(
            {
                "normalize_text": normalize_reply(LONG_ENOUGH),
                "structure": structure_reply(LONG_ENOUGH),
                "triage": triage_reply(0.4),
            }
        )
        result = preprocess(
            make_event([("USER", LONG_ENOUGH)]),
            lightweight=light,
            thinking=thinking,
            config=config_with(coherence_threshold=0.4),
        )

        assert result.episodes[0].entry_class == EntryClass.REFLECTION
        assert result.quality_gate_decision == QualityGateDecision.REFLECTION


class TestSessionVerdict:
    def test_one_good_topic_among_thin_ones_makes_the_session_a_reflection(
        self, make_event, scripted_providers
    ):
        light, thinking = scripted_providers(
            {
                "normalize_text": normalize_reply(LONG_ENOUGH),
                "structure": structure_reply("a real reflection", "an aside", "another"),
                "triage": triage_reply(0.8, 0.1, 0.2),
            }
        )
        result = preprocess(
            make_event([("USER", LONG_ENOUGH)]), lightweight=light, thinking=thinking
        )

        assert result.quality_gate_decision == QualityGateDecision.REFLECTION
        assert [episode.entry_class for episode in result.episodes] == [
            EntryClass.REFLECTION,
            EntryClass.RAW_CAPTURE,
            EntryClass.RAW_CAPTURE,
        ]

    def test_all_thin_topics_make_a_light_session(self, make_event, scripted_providers):
        light, thinking = scripted_providers(
            {
                "normalize_text": normalize_reply(LONG_ENOUGH),
                "structure": structure_reply("one", "two"),
                "triage": triage_reply(0.1, 0.2),
            }
        )
        result = preprocess(
            make_event([("USER", LONG_ENOUGH)]), lightweight=light, thinking=thinking
        )

        assert result.quality_gate_decision == QualityGateDecision.RAW_CAPTURE


class TestReflectionPromptCollection:
    def test_only_questions_for_thin_topics_are_kept(
        self, make_event, scripted_providers
    ):
        triage = json.dumps(
            {
                "scores": [
                    {
                        "episode_index": 1,
                        "coherence_score": 0.9,
                        "reflection_prompts": ["should not appear"],
                    },
                    {
                        "episode_index": 2,
                        "coherence_score": 0.1,
                        "reflection_prompts": ["should appear"],
                    },
                ]
            }
        )
        light, thinking = scripted_providers(
            {
                "normalize_text": normalize_reply(LONG_ENOUGH),
                "structure": structure_reply("good one", "thin one"),
                "triage": triage,
            }
        )
        result = preprocess(
            make_event([("USER", LONG_ENOUGH)]), lightweight=light, thinking=thinking
        )

        assert result.pending_reflections == ["should appear"]

    def test_repeated_questions_are_collapsed_keeping_their_order(
        self, make_event, scripted_providers
    ):
        triage = json.dumps(
            {
                "scores": [
                    {
                        "episode_index": 1,
                        "coherence_score": 0.1,
                        "reflection_prompts": ["first", "shared"],
                    },
                    {
                        "episode_index": 2,
                        "coherence_score": 0.1,
                        "reflection_prompts": ["shared", "second"],
                    },
                ]
            }
        )
        light, thinking = scripted_providers(
            {
                "normalize_text": normalize_reply(LONG_ENOUGH),
                "structure": structure_reply("one", "two"),
                "triage": triage,
            }
        )
        result = preprocess(
            make_event([("USER", LONG_ENOUGH)]), lightweight=light, thinking=thinking
        )

        assert result.pending_reflections == ["first", "shared", "second"]
