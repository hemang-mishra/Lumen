"""
Tests for the preprocessing steps that ask a language model something.

Two things get checked for each step: that a good reply is read correctly,
and that a bad one is survived. The second is the larger half, because a
model call can fail for reasons nobody controls and none of them are a
reason to lose someone's journal entry.

A reply can go wrong in three ways, and each step is checked against all
three: the call itself fails, the reply is not JSON, or the JSON is the
wrong shape.
"""

from __future__ import annotations

import json

import pytest

from lumen.config import PipelineConfig
from lumen.pipeline.preprocessing import passes
from lumen.pipeline.preprocessing.contracts import SegmentedEpisode
from lumen.providers.errors import ProviderTimeoutError
from lumen.providers.fake import FakeLLMProvider
from lumen.schemas.enums import DialogueAct
from lumen.schemas.pipeline import BufferMessage

CONFIG = PipelineConfig()

# The three ways a reply can be unusable, each expressed as a script the
# fake provider will follow.
BROKEN_SCRIPTS = {
    "not_json": ["this is prose, not JSON"],
    "wrong_shape": [json.dumps({"unexpected": "keys only"})],
}


def failing_provider() -> FakeLLMProvider:
    """A model that fails the call itself rather than returning anything."""

    def _raise(_prompt: str) -> str:
        raise ProviderTimeoutError("took too long", provider="fake")

    return FakeLLMProvider(_raise)


def broken_providers() -> list[FakeLLMProvider]:
    """One provider per way a reply can be unusable."""
    return [
        failing_provider(),
        FakeLLMProvider(BROKEN_SCRIPTS["not_json"]),
        FakeLLMProvider(BROKEN_SCRIPTS["wrong_shape"]),
    ]


def messages(pairs) -> list[BufferMessage]:
    from datetime import UTC, date, datetime

    return [
        BufferMessage(
            message_id=f"m{index}",
            role=role,
            content=content,
            timestamp=datetime(2026, 6, 11, 21, index, tzinfo=UTC),
            event_date=date(2026, 6, 11),
        )
        for index, (role, content) in enumerate(pairs)
    ]


class TestConversation:
    def test_the_persons_own_words_are_what_moves_forward(self):
        """
        This is the whole point of the step.

        It used to hand on a summary of what the conversation settled on, and
        everything downstream read that instead of the conversation — an
        evening of thinking arriving at extraction as a paragraph, with the
        record keeping where somebody landed and nothing of how they got
        there.
        """
        provider = FakeLLMProvider(
            [
                json.dumps(
                    {
                        "turns": [
                            {
                                "message_id": "m0",
                                "dialogue_act": "EXPRESSIVE",
                                "co_created_marker": False,
                            },
                            {
                                "message_id": "m2",
                                "dialogue_act": "EXPRESSIVE",
                                "co_created_marker": True,
                            },
                        ],
                        "assistant_digests": [
                            {"message_id": "m1", "digest": "asked what made it hard"}
                        ],
                        "session_summary": "I was avoiding the tradeoff.",
                    }
                )
            ]
        )
        result = passes.run_conversation(
            messages([("USER", "rough day"), ("AI", "why?"), ("USER", "avoiding it")]),
            provider=provider,
        )

        assert "rough day" in result.entry_text
        assert "avoiding it" in result.entry_text
        assert result.settled_summary == "I was avoiding the tradeoff."
        assert result.turn_acts["m0"] == DialogueAct.EXPRESSIVE
        assert result.co_created_message_ids == ("m2",)
        assert result.used_fallback is False

    def test_the_assistant_is_kept_but_condensed(self):
        """
        Half of what a person says is an answer to what was just asked, so
        the assistant's side has to be there — in a sentence, not in the
        several hundred words it was written in.
        """
        provider = FakeLLMProvider(
            [
                json.dumps(
                    {
                        "turns": [
                            {"message_id": "m0", "dialogue_act": "EXPRESSIVE"},
                        ],
                        "assistant_digests": [
                            {"message_id": "m1", "digest": "asked what made it hard"}
                        ],
                        "session_summary": "",
                    }
                )
            ]
        )
        result = passes.run_conversation(
            messages([("USER", "rough day"), ("AI", "A" * 400)]),
            provider=provider,
        )

        assert "asked what made it hard" in result.entry_text
        assert "A" * 400 not in result.entry_text

    def test_an_assistant_turn_nobody_condensed_is_left_out(self):
        """Better a gap than several hundred words of someone else's prose."""
        provider = FakeLLMProvider(
            [
                json.dumps(
                    {
                        "turns": [{"message_id": "m0", "dialogue_act": "EXPRESSIVE"}],
                        "assistant_digests": [],
                        "session_summary": "",
                    }
                )
            ]
        )
        result = passes.run_conversation(
            messages([("USER", "rough day"), ("AI", "unsummarised assistant prose")]),
            provider=provider,
        )

        assert "unsummarised assistant prose" not in result.entry_text
        assert "rough day" in result.entry_text

    def test_a_request_for_information_is_dropped(self):
        """Using the system is not confiding in it."""
        provider = FakeLLMProvider(
            [
                json.dumps(
                    {
                        "turns": [
                            {"message_id": "m0", "dialogue_act": "OPERATIONAL_REQUEST"},
                            {"message_id": "m1", "dialogue_act": "EXPRESSIVE"},
                        ],
                        "session_summary": "",
                    }
                )
            ]
        )
        result = passes.run_conversation(
            messages(
                [("USER", "what did I say yesterday?"), ("USER", "I felt hollow")]
            ),
            provider=provider,
        )

        assert "what did I say yesterday?" not in result.entry_text
        assert "I felt hollow" in result.entry_text

    def test_their_words_are_never_reworded_on_the_way_through(self):
        """
        The person's text is copied out of the buffer, not returned by the
        model, so there is no path by which a model's phrasing can replace
        theirs — however the reply is shaped.
        """
        written = "I just went quiet. Like I always do. And I hate that about myself."
        provider = FakeLLMProvider(
            [
                json.dumps(
                    {
                        "turns": [{"message_id": "m0", "dialogue_act": "EXPRESSIVE"}],
                        "assistant_digests": [
                            {"message_id": "m0", "digest": "a tidied version"}
                        ],
                        "session_summary": "I withdraw under pressure.",
                    }
                )
            ]
        )

        result = passes.run_conversation(
            messages([("USER", written)]), provider=provider
        )

        assert written in result.entry_text

    def test_the_wording_the_person_took_up_is_carried_out(self):
        # Knowing which message showed agreement is not enough later on. The
        # summary replaces the conversation, so unless the assistant's actual
        # phrasing leaves here, nothing downstream can tell whose idea it was.
        provider = FakeLLMProvider(
            [
                json.dumps(
                    {
                        "turns": [
                            {
                                "message_id": "m2",
                                "dialogue_act": "EXPRESSIVE",
                                "co_created_marker": True,
                            }
                        ],
                        "session_summary": "Avoidance is the cost of the tradeoff.",
                        "co_created_spans": ["  avoidance is the cost  ", "   "],
                    }
                )
            ]
        )

        result = passes.run_conversation(
            messages([("USER", "rough day"), ("AI", "avoidance is the cost"), ("USER", "yes, exactly")]),
            provider=provider,
        )

        assert result.co_created_spans == ("avoidance is the cost",)

    def test_no_adopted_wording_is_the_normal_case(self):
        provider = FakeLLMProvider(
            [json.dumps({"turns": [], "session_summary": "I was avoiding it."})]
        )

        result = passes.run_conversation(
            messages([("USER", "rough day"), ("AI", "why?")]), provider=provider
        )

        assert result.co_created_spans == ()

    def test_a_failed_reading_credits_nothing_to_the_assistant(self):
        # Ideas credited to the assistant are trusted less when the history
        # is searched, so guessing at them would quietly demote the person's
        # own words.
        provider = FakeLLMProvider(["not json"])

        result = passes.run_conversation(
            messages([("USER", "rough day"), ("AI", "why?")]), provider=provider
        )

        assert result.co_created_spans == ()
        assert result.used_fallback is True

    def test_the_assistant_side_is_shown_to_the_model(self):
        provider = FakeLLMProvider([json.dumps({"turns": [], "session_summary": "x"})])
        passes.run_conversation(
            messages([("USER", "mine"), ("AI", "assistant words")]),
            provider=provider,
        )
        # The assistant's turns are needed to judge what the person took up,
        # even though they are never extracted from.
        assert "assistant words" in provider.calls[0].prompt

    def test_a_session_of_nothing_but_requests_comes_back_empty(self):
        provider = FakeLLMProvider(
            [
                json.dumps(
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
                )
            ]
        )
        result = passes.run_conversation(
            messages([("USER", "what did I say yesterday?")]),
            provider=provider,
        )

        assert result.entry_text == ""
        assert result.used_fallback is False

    def test_filtering_that_removes_a_real_reflection_puts_it_back(self):
        """
        Classifying every expressive turn as a request would empty the entry.
        Their words go back in rather than the evening being lost.
        """
        provider = FakeLLMProvider(
            [
                json.dumps(
                    {
                        "turns": [
                            {
                                "message_id": "m0",
                                "dialogue_act": "OPERATIONAL_REQUEST",
                                "co_created_marker": False,
                            },
                            {
                                "message_id": "m1",
                                "dialogue_act": "EXPRESSIVE",
                                "co_created_marker": False,
                            },
                        ],
                        "session_summary": "   ",
                    }
                )
            ]
        )
        # The one expressive verdict names a message that is not in the
        # buffer, so filtering leaves nothing behind.
        result = passes.run_conversation(
            messages([("USER", "I felt hollow all day")]),
            provider=provider,
        )

        assert result.entry_text == "I felt hollow all day"
        assert result.used_fallback is True

    @pytest.mark.parametrize("provider", broken_providers())
    def test_a_broken_reply_keeps_every_word_the_person_wrote(self, provider):
        result = passes.run_conversation(
            messages([("USER", "first thing"), ("AI", "ignore me"), ("USER", "second")]),
            provider=provider,
        )

        assert result.entry_text == "first thing\n\nsecond"
        assert result.used_fallback is True

    def test_the_fallback_is_logged(self, captured_logs):
        passes.run_conversation(
            messages([("USER", "something")]),
            provider=failing_provider(),
        )
        warnings = [line for line in captured_logs if line["level"] == "WARNING"]
        assert any(line.get("preprocessing_pass") == "conversation" for line in warnings)


class TestNormalize:
    def test_returns_cleaned_text_and_what_was_translated(self):
        provider = FakeLLMProvider(
            [
                json.dumps(
                    {
                        "cleaned_text": "I did not understand him.",
                        "detected_languages": ["en", "hi"],
                        "translated": True,
                    }
                )
            ]
        )
        result = passes.run_normalize(
            "mujhe samajh nahi aaya", is_voice=False, provider=provider
        )

        assert result.text == "I did not understand him."
        assert result.detected_languages == ("en", "hi")
        assert result.translated is True
        assert result.used_fallback is False

    def test_speech_gets_the_hesitations_stripped_before_the_model_sees_it(self):
        provider = FakeLLMProvider([json.dumps({"cleaned_text": "I was frustrated"})])
        result = passes.run_normalize(
            "I was um frustrated", is_voice=True, provider=provider
        )

        assert result.fillers_removed == 1
        assert "I was frustrated" in provider.calls[0].prompt
        assert "I was um frustrated" not in provider.calls[0].prompt

    def test_typed_text_keeps_its_hesitations(self):
        provider = FakeLLMProvider([json.dumps({"cleaned_text": "I was um frustrated"})])
        result = passes.run_normalize(
            "I was um frustrated", is_voice=False, provider=provider
        )

        assert result.fillers_removed == 0
        assert "I was um frustrated" in provider.calls[0].prompt

    def test_speech_and_typing_are_given_different_instructions(self):
        spoken = FakeLLMProvider([json.dumps({"cleaned_text": "x"})])
        typed = FakeLLMProvider([json.dumps({"cleaned_text": "x"})])
        passes.run_normalize("text", is_voice=True, provider=spoken)
        passes.run_normalize("text", is_voice=False, provider=typed)

        assert "self-correction" in spoken.calls[0].prompt
        assert "self-correction" not in typed.calls[0].prompt
        assert "typed, not spoken" in typed.calls[0].prompt

    @pytest.mark.parametrize("provider", broken_providers())
    def test_a_broken_reply_keeps_the_text_as_it_arrived(self, provider):
        result = passes.run_normalize(
            "the original words", is_voice=False, provider=provider
        )

        assert result.text == "the original words"
        assert result.used_fallback is True

    def test_a_reply_that_ate_the_text_is_treated_as_a_failure(self):
        provider = FakeLLMProvider([json.dumps({"cleaned_text": "   "})])
        result = passes.run_normalize(
            "real content here", is_voice=False, provider=provider
        )

        assert result.text == "real content here"
        assert result.used_fallback is True

    def test_empty_input_returning_empty_is_not_a_failure(self):
        provider = FakeLLMProvider([json.dumps({"cleaned_text": ""})])
        result = passes.run_normalize(
            "   ", is_voice=False, provider=provider
        )

        assert result.text == ""
        assert result.used_fallback is False


class TestStructure:
    def _reply(self, episodes, coreference=None):
        return json.dumps(
            {
                "episodes": episodes,
                "coreference": coreference
                or {"resolved_entities": [], "ambiguous_refs": []},
            }
        )

    def test_returns_topics_and_the_resolved_references(self):
        provider = FakeLLMProvider(
            [
                self._reply(
                    [
                        {
                            "episode_summary": "The mentor conflict",
                            "text": "Jordan pushed back on the plan.",
                            "overarching_themes": ["Work"],
                            "historical_era": "exam prep",
                        }
                    ],
                    {
                        "resolved_entities": [
                            {
                                "span": "he",
                                "resolved_to": "Jordan",
                                "confidence": 0.9,
                                "resolution_basis": "most_recent_named_antecedent",
                            }
                        ],
                        "ambiguous_refs": [],
                    },
                )
            ]
        )
        result = passes.run_structure(
            "text", entry_id="sess_1", provider=provider, config=CONFIG
        )

        assert len(result.episodes) == 1
        assert result.episodes[0].historical_era == "exam prep"
        assert result.coreference_map.entry_id == "sess_1"
        assert result.coreference_map.resolved_entities[0].resolved_to == "Jordan"
        assert result.used_fallback is False

    @pytest.mark.parametrize("provider", broken_providers())
    def test_a_broken_reply_keeps_the_entry_whole(self, provider):
        result = passes.run_structure(
            "the whole entry text", entry_id="sess_1", provider=provider, config=CONFIG
        )

        assert len(result.episodes) == 1
        assert result.episodes[0].text == "the whole entry text"
        assert result.coreference_map.resolved_entities == []
        assert result.used_fallback is True

    def test_zero_topics_is_treated_as_a_failure(self):
        provider = FakeLLMProvider([self._reply([])])
        result = passes.run_structure(
            "real text", entry_id="sess_1", provider=provider, config=CONFIG
        )

        assert len(result.episodes) == 1
        assert result.episodes[0].text == "real text"
        assert result.used_fallback is True

    def test_too_many_topics_are_folded_into_the_last_one(self):
        episodes = [
            {
                "episode_summary": f"topic {index}",
                "text": f"text {index}",
                "overarching_themes": [f"theme{index}"],
            }
            for index in range(6)
        ]
        provider = FakeLLMProvider([self._reply(episodes)])
        result = passes.run_structure(
            "text",
            entry_id="sess_1",
            provider=provider,
            config=PipelineConfig(max_episodes_per_session=3),
        )

        assert len(result.episodes) == 3
        assert result.overflow_merged is True
        # Every word survives the fold.
        assert result.episodes[2].text == "text 2\n\ntext 3\n\ntext 4\n\ntext 5"
        assert result.episodes[2].overarching_themes == [
            "theme2",
            "theme3",
            "theme4",
            "theme5",
        ]

    def test_an_era_survives_the_fold(self):
        episodes = [
            {"episode_summary": "a", "text": "a", "historical_era": None},
            {"episode_summary": "b", "text": "b", "historical_era": None},
            {"episode_summary": "c", "text": "c", "historical_era": "college"},
        ]
        provider = FakeLLMProvider([self._reply(episodes)])
        result = passes.run_structure(
            "text",
            entry_id="sess_1",
            provider=provider,
            config=PipelineConfig(max_episodes_per_session=2),
        )

        assert result.episodes[1].historical_era == "college"

    def test_a_split_within_the_limit_is_left_alone(self):
        episodes = [
            {"episode_summary": "a", "text": "a"},
            {"episode_summary": "b", "text": "b"},
        ]
        provider = FakeLLMProvider([self._reply(episodes)])
        result = passes.run_structure(
            "text",
            entry_id="sess_1",
            provider=provider,
            config=PipelineConfig(max_episodes_per_session=2),
        )

        assert len(result.episodes) == 2
        assert result.overflow_merged is False


class TestTriage:
    def test_scores_are_matched_to_topics_by_position(self):
        provider = FakeLLMProvider(
            [
                json.dumps(
                    {
                        "scores": [
                            {
                                "episode_index": 2,
                                "coherence_score": 0.2,
                                "reason": "thin",
                                "reflection_prompts": ["what happened?"],
                            },
                            {
                                "episode_index": 1,
                                "coherence_score": 0.9,
                                "reason": "clear",
                                "reflection_prompts": [],
                            },
                        ]
                    }
                )
            ]
        )
        result = passes.run_triage(["first", "second"], provider=provider, config=CONFIG)

        # Returned out of order by the model, put back in order here.
        assert [score.coherence_score for score in result.scores] == [0.9, 0.2]
        assert result.used_fallback is False

    def test_no_topics_means_no_call_and_no_scores(self):
        provider = FakeLLMProvider([])
        result = passes.run_triage([], provider=provider, config=CONFIG)

        assert result.scores == ()
        assert provider.calls == []

    @pytest.mark.parametrize("provider", broken_providers())
    def test_a_broken_reply_treats_everything_as_thin(self, provider):
        result = passes.run_triage(["a", "b"], provider=provider, config=CONFIG)

        assert [score.coherence_score for score in result.scores] == [0.0, 0.0]
        assert result.used_fallback is True

    def test_scores_for_topics_that_do_not_exist_count_as_no_match(self):
        provider = FakeLLMProvider(
            [json.dumps({"scores": [{"episode_index": 9, "coherence_score": 0.9}]})]
        )
        result = passes.run_triage(["only one"], provider=provider, config=CONFIG)

        assert result.used_fallback is True
        assert result.scores[0].coherence_score == 0.0

    def test_a_topic_the_model_skipped_is_left_unscored(self, captured_logs):
        provider = FakeLLMProvider(
            [json.dumps({"scores": [{"episode_index": 1, "coherence_score": 0.9}]})]
        )
        result = passes.run_triage(["a", "b"], provider=provider, config=CONFIG)

        assert [score.coherence_score for score in result.scores] == [0.9, 0.0]
        assert result.used_fallback is False
        assert any("without a score" in line["msg"] for line in captured_logs)

    def test_extra_questions_are_trimmed_to_the_agreed_number(self):
        provider = FakeLLMProvider(
            [
                json.dumps(
                    {
                        "scores": [
                            {
                                "episode_index": 1,
                                "coherence_score": 0.1,
                                "reflection_prompts": ["a", "b", "c", "d", "e"],
                            }
                        ]
                    }
                )
            ]
        )
        result = passes.run_triage(
            ["text"], provider=provider, config=PipelineConfig(reflection_prompt_count=3)
        )

        assert result.scores[0].reflection_prompts == ["a", "b", "c"]

    def test_blank_questions_are_dropped(self):
        provider = FakeLLMProvider(
            [
                json.dumps(
                    {
                        "scores": [
                            {
                                "episode_index": 1,
                                "coherence_score": 0.1,
                                "reflection_prompts": ["real question", "   ", ""],
                            }
                        ]
                    }
                )
            ]
        )
        result = passes.run_triage(["text"], provider=provider, config=CONFIG)

        assert result.scores[0].reflection_prompts == ["real question"]

    def test_the_threshold_and_count_reach_the_prompt(self):
        provider = FakeLLMProvider([json.dumps({"scores": []})])
        passes.run_triage(
            ["text"],
            provider=provider,
            config=PipelineConfig(coherence_threshold=0.55, reflection_prompt_count=4),
        )

        assert "0.55" in provider.calls[0].prompt
        assert "exactly 4" in provider.calls[0].prompt


class TestReflectionPrompts:
    def test_returns_the_questions(self):
        provider = FakeLLMProvider(
            [json.dumps({"reflection_prompts": ["what bothered you?", "why now?"]})]
        )
        result = passes.run_reflection_prompts(
            "short entry", provider=provider, config=CONFIG
        )

        assert result.prompts == ("what bothered you?", "why now?")
        assert result.used_fallback is False

    def test_extra_questions_are_trimmed(self):
        provider = FakeLLMProvider(
            [json.dumps({"reflection_prompts": ["a", "b", "c", "d"]})]
        )
        result = passes.run_reflection_prompts(
            "short", provider=provider, config=PipelineConfig(reflection_prompt_count=2)
        )

        assert result.prompts == ("a", "b")

    @pytest.mark.parametrize("provider", broken_providers())
    def test_a_broken_reply_offers_nothing_rather_than_something_generic(self, provider):
        result = passes.run_reflection_prompts("short", provider=provider, config=CONFIG)

        assert result.prompts == ()
        assert result.used_fallback is True


class TestLocalSummary:
    def test_short_text_is_used_whole(self):
        assert passes.local_summary("A short line") == "A short line"

    def test_long_text_is_cut_and_marked(self):
        summary = passes.local_summary("word " * 40)
        assert summary.endswith("…")
        assert len(summary) <= 81

    def test_line_breaks_are_flattened(self):
        assert passes.local_summary("first\n\nsecond") == "first second"

    def test_empty_text_gets_an_honest_label(self):
        assert passes.local_summary("   ") == "Empty entry"


class TestWholeTextEpisode:
    def test_wraps_the_text_with_a_summary_taken_from_it(self):
        episode = passes.whole_text_episode("The entire entry goes here.")

        assert isinstance(episode, SegmentedEpisode)
        assert episode.text == "The entire entry goes here."
        assert episode.episode_summary == "The entire entry goes here."


class TestPrivacy:
    @pytest.mark.parametrize("provider", broken_providers())
    def test_a_failure_never_logs_what_was_being_read(self, provider, captured_logs):
        secret = "the specific thing I have never told anyone"
        passes.run_normalize(secret, is_voice=False, provider=provider)

        assert not any(secret in json.dumps(line) for line in captured_logs)


class TestSplittingAConversationByTurn:
    """
    Splitting a conversation by naming its turns rather than repeating them.

    Asking a model to hand a whole evening back, divided up, costs as much
    output as the evening was long — which runs into the reply limit, where
    the failure is a quietly truncated entry — and gives it an opening to
    reword somebody's writing on the way past. Numbers cannot be reworded.
    """

    def _turns(self, pairs):
        return [
            (message, message.content)
            for message in messages(pairs)
        ]

    def _reply(self, episodes, coreference=None):
        return json.dumps(
            {
                "episodes": episodes,
                "coreference": coreference
                or {"resolved_entities": [], "ambiguous_refs": []},
            }
        )

    def test_the_writing_is_put_back_together_from_turn_numbers(self):
        turns = self._turns(
            [
                ("USER", "the argument about the deadline"),
                ("AI", "asked what was said"),
                ("USER", "and separately, my brother called"),
            ]
        )
        provider = FakeLLMProvider(
            [
                self._reply(
                    [
                        {"episode_summary": "Work", "turn_numbers": [1, 2]},
                        {"episode_summary": "Family", "turn_numbers": [3]},
                    ]
                )
            ]
        )

        result = passes.run_structure_by_turns(
            turns, entry_id="s1", provider=provider, config=CONFIG
        )

        assert [episode.episode_summary for episode in result.episodes] == [
            "Work",
            "Family",
        ]
        assert "deadline" in result.episodes[0].text
        assert "brother" in result.episodes[1].text
        assert "brother" not in result.episodes[0].text

    def test_the_conversation_is_never_sent_back_for_repeating(self):
        turns = self._turns([("USER", "something"), ("USER", "something else")])
        provider = FakeLLMProvider(
            [self._reply([{"episode_summary": "One", "turn_numbers": [1, 2]}])]
        )

        passes.run_structure_by_turns(
            turns, entry_id="s1", provider=provider, config=CONFIG
        )

        assert "turn_numbers" not in provider.calls[0].prompt
        assert "[1] ME:" in provider.calls[0].prompt

    def test_a_topic_returned_to_later_keeps_both_halves(self):
        """People wander off a subject and come back to it."""
        turns = self._turns(
            [
                ("USER", "about the deadline"),
                ("USER", "unrelated aside"),
                ("USER", "back to the deadline"),
            ]
        )
        provider = FakeLLMProvider(
            [
                self._reply(
                    [
                        {"episode_summary": "Deadline", "turn_numbers": [1, 3]},
                        {"episode_summary": "Aside", "turn_numbers": [2]},
                    ]
                )
            ]
        )

        result = passes.run_structure_by_turns(
            turns, entry_id="s1", provider=provider, config=CONFIG
        )

        assert "about the deadline" in result.episodes[0].text
        assert "back to the deadline" in result.episodes[0].text

    def test_a_turn_nobody_placed_is_kept_rather_than_dropped(self):
        """Losing part of an entry is the one outcome this must never have."""
        turns = self._turns([("USER", "placed"), ("USER", "forgotten entirely")])
        provider = FakeLLMProvider(
            [self._reply([{"episode_summary": "One", "turn_numbers": [1]}])]
        )

        result = passes.run_structure_by_turns(
            turns, entry_id="s1", provider=provider, config=CONFIG
        )

        written = " ".join(episode.text for episode in result.episodes)
        assert "forgotten entirely" in written

    def test_a_turn_claimed_twice_lands_in_one_topic_only(self):
        """Duplicating the writing would double-count it everywhere after."""
        turns = self._turns([("USER", "said once")])
        provider = FakeLLMProvider(
            [
                self._reply(
                    [
                        {"episode_summary": "First", "turn_numbers": [1]},
                        {"episode_summary": "Second", "turn_numbers": [1]},
                    ]
                )
            ]
        )

        result = passes.run_structure_by_turns(
            turns, entry_id="s1", provider=provider, config=CONFIG
        )

        written = [episode.text for episode in result.episodes]
        assert sum("said once" in text for text in written) == 1

    def test_a_number_naming_no_turn_is_ignored(self):
        turns = self._turns([("USER", "the only turn")])
        provider = FakeLLMProvider(
            [self._reply([{"episode_summary": "One", "turn_numbers": [1, 47]}])]
        )

        result = passes.run_structure_by_turns(
            turns, entry_id="s1", provider=provider, config=CONFIG
        )

        assert len(result.episodes) == 1
        assert result.episodes[0].text.endswith("the only turn")

    @pytest.mark.parametrize("provider", broken_providers())
    def test_a_broken_reply_keeps_the_conversation_whole(self, provider):
        turns = self._turns([("USER", "first"), ("USER", "second")])

        result = passes.run_structure_by_turns(
            turns, entry_id="s1", provider=provider, config=CONFIG
        )

        assert len(result.episodes) == 1
        assert "first" in result.episodes[0].text
        assert "second" in result.episodes[0].text
        assert result.used_fallback is True

    def test_the_references_come_back_too(self):
        turns = self._turns([("USER", "Jordan pushed back, then he left")])
        provider = FakeLLMProvider(
            [
                self._reply(
                    [{"episode_summary": "One", "turn_numbers": [1]}],
                    {
                        "resolved_entities": [
                            {
                                "span": "he",
                                "resolved_to": "Jordan",
                                "confidence": 0.9,
                                "resolution_basis": "most_recent_named_antecedent",
                            }
                        ],
                        "ambiguous_refs": [],
                    },
                )
            ]
        )

        result = passes.run_structure_by_turns(
            turns, entry_id="s1", provider=provider, config=CONFIG
        )

        assert result.coreference_map.resolved_entities[0].resolved_to == "Jordan"
