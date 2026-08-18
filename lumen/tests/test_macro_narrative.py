"""
Tests for the model call that writes a report's sentences.

Two properties are being defended here and they are the reason this module
exists at all.

A model writing about somebody's history will occasionally attach a sentence
to an identifier it made up, and an invented identifier fails silently — the
sentence reads perfectly and belongs to nothing. So every reference is checked
and the report records when references were thrown away.

And a failed call must never cost a period. The counting is finished before
this runs, and a period is only ever reported on once, so losing it to a model
outage would lose it permanently.
"""

from __future__ import annotations

import json

from lumen.config import MacroConfig
from lumen.pipeline.macroextraction import analytics, narrative
from lumen.pipeline.macroextraction.contracts import (
    ArchetypeNarrative,
    ContradictionPrompt,
    EnvironmentGroup,
    GapJudgement,
    NarrativeDraft,
    RelationalSummary,
    ShadowFinding,
)
from lumen.providers.errors import ProviderError
from lumen.providers.fake import FakeLLMProvider
from lumen.schemas.enums import ModelRole, NarrativeStatus, ObservationType


class BrokenProvider:
    """A model that cannot be reached at all."""

    provider_name = "broken"
    model_name = "broken-model"
    model_role = ModelRole.THINKING

    def generate_structured(self, *args, **kwargs):
        raise ProviderError("the model is unreachable")

    def generate_text(self, *args, **kwargs):  # pragma: no cover - never called
        raise ProviderError("the model is unreachable")

    def close(self) -> None:  # pragma: no cover - nothing to release
        return None


def facts_for(corpus) -> object:
    """The counted half of a report, which is what the model is shown."""
    return analytics.compute(corpus, config=MacroConfig())


class TestWhenTheModelAnswers:
    def test_the_wording_is_kept_and_marked_sound(
        self, make_corpus, make_episode_facts, narrative_provider
    ):
        facts = facts_for(make_corpus(episodes=[make_episode_facts("ep_1")]))
        provider = narrative_provider({"headline": "A quiet month, mostly."})

        written = narrative.write(facts, provider=provider, config=MacroConfig())

        assert written.status is NarrativeStatus.OK
        assert written.draft.headline == "A quiet month, mostly."
        assert written.model_used == "fake-thinker"

    def test_an_extra_field_nobody_asked_for_is_ignored_not_refused(
        self, make_corpus, make_episode_facts, narrative_provider
    ):
        # A model that adds a field has still answered the question, and
        # throwing the reply away would cost the report all of its prose.
        facts = facts_for(make_corpus(episodes=[make_episode_facts("ep_1")]))
        provider = narrative_provider({"headline": "Fine.", "mood_score": 0.4})

        written = narrative.write(facts, provider=provider, config=MacroConfig())

        assert written.status is NarrativeStatus.OK
        assert written.draft.headline == "Fine."


class TestWhenTheModelCannotBeTrusted:
    def test_a_sentence_about_someone_who_was_not_listed_is_dropped(
        self, make_corpus, make_episode_facts
    ):
        facts = facts_for(make_corpus(episodes=[make_episode_facts("ep_1")]))
        draft = NarrativeDraft(
            relational_summaries=[
                RelationalSummary(person_ref="Nobody", dynamic_summary="a fine week")
            ]
        )

        cleaned, dropped = narrative.keep_known_references(draft, facts)

        assert cleaned.relational_summaries == []
        assert dropped == 1

    def test_a_judgement_about_a_gap_that_does_not_exist_is_dropped(
        self, make_corpus, make_episode_facts
    ):
        facts = facts_for(make_corpus(episodes=[make_episode_facts("ep_1")]))
        draft = NarrativeDraft(
            biographical_gaps=[GapJudgement(observation_id="obs_invented")]
        )

        cleaned, dropped = narrative.keep_known_references(draft, facts)

        assert cleaned.biographical_gaps == []
        assert dropped == 1

    def test_a_question_about_a_tension_that_does_not_exist_is_dropped(
        self, make_corpus, make_episode_facts
    ):
        facts = facts_for(make_corpus(episodes=[make_episode_facts("ep_1")]))
        draft = NarrativeDraft(
            contradiction_prompts=[
                ContradictionPrompt(contradiction_id="con_invented", reflection_prompt="?")
            ]
        )

        cleaned, dropped = narrative.keep_known_references(draft, facts)

        assert cleaned.contradiction_prompts == []
        assert dropped == 1

    def test_a_group_keeps_the_real_notes_and_loses_the_invented_ones(
        self, make_corpus, make_episode_facts, make_observation_facts
    ):
        observation = make_observation_facts(
            "obs_real",
            observation_type=ObservationType.ENVIRONMENTAL_DEPENDENCY,
            episode_id="ep_1",
        )
        facts = facts_for(
            make_corpus(
                episodes=[make_episode_facts("ep_1", observations=(observation,))]
            )
        )
        draft = NarrativeDraft(
            environment_groups=[
                EnvironmentGroup(
                    environment="The office",
                    dependency="focus",
                    observation_ids=["obs_real", "obs_invented"],
                )
            ]
        )

        cleaned, dropped = narrative.keep_known_references(draft, facts)

        assert cleaned.environment_groups[0].observation_ids == ["obs_real"]
        assert dropped == 1

    def test_a_group_built_entirely_from_invention_is_dropped_whole(
        self, make_corpus, make_episode_facts
    ):
        facts = facts_for(make_corpus(episodes=[make_episode_facts("ep_1")]))
        draft = NarrativeDraft(
            environment_groups=[
                EnvironmentGroup(environment="Nowhere", observation_ids=["obs_invented"])
            ]
        )

        cleaned, dropped = narrative.keep_known_references(draft, facts)

        assert cleaned.environment_groups == []
        assert dropped == 2

    def test_a_shift_the_arithmetic_did_not_find_is_refused(
        self, make_corpus, make_episode_facts
    ):
        # The single most consequential line a report can contain. It must
        # never be reachable from prose alone.
        facts = facts_for(make_corpus(episodes=[make_episode_facts("ep_1")]))
        draft = NarrativeDraft(
            archetype_shift=ArchetypeNarrative(shift_label="Fear → Freedom")
        )

        cleaned, dropped = narrative.keep_known_references(draft, facts)

        assert cleaned.archetype_shift is None
        assert dropped == 1

    def test_dropping_anything_marks_the_wording_incomplete(
        self, make_corpus, make_episode_facts, narrative_provider
    ):
        facts = facts_for(make_corpus(episodes=[make_episode_facts("ep_1")]))
        provider = narrative_provider(
            {
                "headline": "A month.",
                "relational_summaries": [
                    {"person_ref": "Nobody", "dynamic_summary": "x"}
                ],
            }
        )

        written = narrative.write(facts, provider=provider, config=MacroConfig())

        assert written.status is NarrativeStatus.DEGRADED
        assert written.dropped_references == 1


class TestWhenTheModelFails:
    def test_an_unreachable_model_leaves_the_counts_untouched(
        self, make_corpus, make_episode_facts
    ):
        facts = facts_for(make_corpus(episodes=[make_episode_facts("ep_1")]))

        written = narrative.write(
            facts, provider=BrokenProvider(), config=MacroConfig()
        )

        assert written.status is NarrativeStatus.UNAVAILABLE
        assert written.draft.headline == ""
        # The report is still perfectly writable; it simply has no prose.
        assert facts.episodes_analyzed == 1

    def test_an_unreadable_reply_is_tried_again(
        self, make_corpus, make_episode_facts
    ):
        facts = facts_for(make_corpus(episodes=[make_episode_facts("ep_1")]))
        provider = FakeLLMProvider(
            ["not json at all", json.dumps({"headline": "Second time lucky."})],
            role=ModelRole.THINKING,
            model="fake-thinker",
        )

        written = narrative.write(facts, provider=provider, config=MacroConfig())

        assert written.status is NarrativeStatus.OK
        assert written.draft.headline == "Second time lucky."

    def test_giving_up_after_the_configured_number_of_tries(
        self, make_corpus, make_episode_facts
    ):
        facts = facts_for(make_corpus(episodes=[make_episode_facts("ep_1")]))
        provider = FakeLLMProvider(
            ["nonsense", "still nonsense"], role=ModelRole.THINKING, model="fake"
        )

        written = narrative.write(
            facts, provider=provider, config=MacroConfig(narrative_attempts=2)
        )

        assert written.status is NarrativeStatus.UNAVAILABLE


class TestWhatTheModelIsShown:
    def test_the_material_names_the_patterns_that_recurred(
        self, make_corpus, make_episode_facts, make_observation_facts, make_link, pattern_row
    ):
        observation = make_observation_facts("obs_1", episode_id="ep_1")
        facts = facts_for(
            make_corpus(
                episodes=[make_episode_facts("ep_1", observations=(observation,))],
                links=[make_link("obs_1", "pat_a")],
                patterns={"pat_a": pattern_row("pat_a", name="Comparison with peers")},
            )
        )

        brief = narrative.build_brief(facts, config=MacroConfig())

        assert "Comparison with peers" in brief
        assert "[pat_a]" in brief

    def test_an_empty_section_is_left_out_rather_than_shown_empty(
        self, make_corpus, make_episode_facts
    ):
        # A heading with nothing under it invites a model to fill it in.
        facts = facts_for(make_corpus(episodes=[make_episode_facts("ep_1")]))

        brief = narrative.build_brief(facts, config=MacroConfig())

        assert "TENSIONS HELD" not in brief

    def test_a_thin_period_still_produces_something_to_answer(
        self, make_corpus, make_episode_facts
    ):
        facts = facts_for(make_corpus(episodes=[make_episode_facts("ep_1")]))

        brief = narrative.build_brief(facts, config=MacroConfig())

        assert brief.strip() != ""

    def test_the_material_is_trimmed_to_fit(
        self, make_corpus, make_episode_facts, make_observation_facts, make_link, pattern_row
    ):
        observations = tuple(
            make_observation_facts(f"obs_{i}", episode_id="ep_1") for i in range(40)
        )
        facts = facts_for(
            make_corpus(
                episodes=[make_episode_facts("ep_1", observations=observations)],
                links=[make_link(f"obs_{i}", f"pat_{i}") for i in range(40)],
                patterns={f"pat_{i}": pattern_row(f"pat_{i}") for i in range(40)},
            )
        )

        brief = narrative.build_brief(facts, config=MacroConfig(narrative_max_chars=500))

        assert len(brief) <= 500

    def test_a_long_quotation_is_shortened_rather_than_left_to_crowd_the_rest(
        self, make_corpus, make_episode_facts, make_observation_facts
    ):
        observation = make_observation_facts(
            "obs_1", content="x" * 2000, signal="HIGH", episode_id="ep_1"
        )
        facts = facts_for(
            make_corpus(
                episodes=[make_episode_facts("ep_1", observations=(observation,))]
            )
        )

        brief = narrative.build_brief(
            facts, config=MacroConfig(narrative_excerpt_chars=60)
        )

        assert "x" * 2000 not in brief
        assert "…" in brief

    def test_a_shift_is_only_shown_when_one_was_found(
        self, make_corpus, make_episode_facts
    ):
        facts = facts_for(make_corpus(episodes=[make_episode_facts("ep_1")]))

        assert "SHIFT DETECTED" not in narrative.build_brief(facts, config=MacroConfig())


class TestDescribingATwoDayBurst:
    def test_the_model_writes_the_description_when_it_can(self):
        finding = ShadowFinding(detected=True, branch_count=3, target_count=3)
        provider = FakeLLMProvider(
            [json.dumps({"shift_type": "Sudden opening up", "summary": "Things moved."})],
            role=ModelRole.LIGHTWEIGHT,
            model="fake-light",
        )

        described = narrative.write_shadow(
            finding, [], provider=provider, config=MacroConfig()
        )

        assert described.shift_type == "Sudden opening up"

    def test_an_unreachable_model_still_produces_an_alert(self):
        # An alert saying plainly that several things shifted is worth
        # raising; silence because nothing could phrase it nicely is not.
        finding = ShadowFinding(detected=True, branch_count=3, target_count=3)

        described = narrative.write_shadow(
            finding, [], provider=BrokenProvider(), config=MacroConfig()
        )

        assert described.summary != ""
        assert "away from established patterns" in described.shift_type

    def test_an_unreadable_reply_falls_back_to_a_plain_description(self):
        finding = ShadowFinding(detected=True, contradict_count=3, target_count=3)
        provider = FakeLLMProvider(
            ["not json"], role=ModelRole.LIGHTWEIGHT, model="fake-light"
        )

        described = narrative.write_shadow(
            finding, [], provider=provider, config=MacroConfig()
        )

        assert "Tension surfacing" in described.shift_type

    def test_a_mixed_burst_is_described_as_both(self):
        finding = ShadowFinding(
            detected=True, branch_count=2, contradict_count=2, target_count=4
        )

        described = narrative.plain_shadow(finding)

        assert "alongside" in described.shift_type


class TestTheSmallHelpers:
    def test_a_thin_period_is_described_rather_than_left_blank(self):
        assert narrative._fit([], limit=100) == "(this period held almost nothing)"

    def test_a_section_that_does_not_fit_is_dropped_whole(self):
        # A half-written list looks to a model like a complete short one.
        blocks = ["A" * 40 + "\n", "B" * 400 + "\n"]

        assert narrative._fit(blocks, limit=100) == "A" * 40 + "\n"

    def test_a_short_line_is_left_alone(self):
        assert narrative._clip("a short line", 100) == "a short line"

    def test_whitespace_in_a_quotation_is_tidied(self):
        assert narrative._clip("two   spaced\n lines", 100) == "two spaced lines"


class TestWhenTheReplyIsReadableButWrongShaped:
    def test_a_field_of_the_wrong_kind_is_treated_as_a_failed_try(
        self, make_corpus, make_episode_facts
    ):
        # Parsed perfectly and still unusable. Asking again is worth one
        # attempt, since nothing is waiting on this.
        facts = facts_for(make_corpus(episodes=[make_episode_facts("ep_1")]))
        provider = FakeLLMProvider(
            [
                json.dumps({"relational_summaries": "not a list"}),
                json.dumps({"headline": "Second time lucky."}),
            ],
            role=ModelRole.THINKING,
            model="fake-thinker",
        )

        written = narrative.write(facts, provider=provider, config=MacroConfig())

        assert written.status is NarrativeStatus.OK
        assert written.draft.headline == "Second time lucky."

    def test_a_wrong_shaped_burst_description_falls_back_to_a_plain_one(self):
        finding = ShadowFinding(detected=True, branch_count=3, target_count=3)
        provider = FakeLLMProvider(
            [json.dumps({"shift_type": {"nested": "object"}})],
            role=ModelRole.LIGHTWEIGHT,
            model="fake-light",
        )

        described = narrative.write_shadow(
            finding, [], provider=provider, config=MacroConfig()
        )

        assert described.summary != ""


class TestDescribingProgress:
    def test_what_was_done_about_a_pattern_is_shown_to_the_model(
        self, make_corpus, make_episode_facts, make_observation_facts, make_link, pattern_row
    ):
        observation = make_observation_facts("obs_1", episode_id="ep_1")
        facts = facts_for(
            make_corpus(
                episodes=[make_episode_facts("ep_1", observations=(observation,))],
                links=[make_link("obs_1", "pat_a", edge_name="regulates_obs")],
                patterns={"pat_a": pattern_row("pat_a", name="Comparison")},
                previous_pattern_episodes={"pat_a": 6},
                decisions=[
                    {"node_id": "d_1", "action": "EVOLVE", "target_node_id": "pat_a"}
                ],
            )
        )

        brief = narrative.build_brief(facts, config=MacroConfig())

        assert "GROWTH CANDIDATE" in brief
        assert "the belief behind it was revised" in brief
        assert "it was interrupted deliberately" in brief

    def test_a_shift_that_was_found_is_shown_with_its_patterns(
        self, make_corpus, make_window, make_episode_facts, pattern_row
    ):
        from lumen.schemas.enums import ReportType

        facts = facts_for(
            make_corpus(
                window=make_window(ReportType.QUARTERLY),
                episodes=[make_episode_facts("ep_1")],
                comparison_counts={f"pat_{i}": 4 for i in range(5)},
                all_patterns=[pattern_row(f"pat_{i}") for i in range(5)],
            )
        )

        brief = narrative.build_brief(facts, config=MacroConfig())

        assert "SHIFT DETECTED" in brief
        assert "frequency decreasing" in brief
