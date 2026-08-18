"""
Tests for joining the counts and the sentences into one document.

The two halves have been kept apart all the way through the package, and this
is where they meet. What is being checked is that they meet by identifier, so
no sentence can end up attached to the wrong figure, and that the halves stay
distinguishable afterwards — a report has to say when its prose is missing,
because a report with no wording and one that was never given any read
identically otherwise.
"""

from __future__ import annotations

from lumen.config import MacroConfig
from lumen.pipeline.macroextraction import analytics, assemble
from lumen.pipeline.macroextraction.contracts import (
    ArchetypeNarrative,
    ArcNarrative,
    ContradictionPrompt,
    EnvironmentGroup,
    GapJudgement,
    NarrativeDraft,
    NarrativeResult,
    RelationalSummary,
    ShadowFinding,
    ShadowNarrative,
)
from lumen.schemas.enums import (
    ArcDirection,
    GapStatus,
    NarrativeStatus,
    ObservationType,
    ReportType,
    SignalStrength,
)


def written(draft: NarrativeDraft | None = None, **overrides) -> NarrativeResult:
    """One narrative result, sound unless a test says otherwise."""
    overrides.setdefault("status", NarrativeStatus.OK)
    overrides.setdefault("model_used", "fake-thinker")
    return NarrativeResult(draft=draft or NarrativeDraft(), **overrides)


class TestWhatAReportIsCalled:
    def test_it_is_named_after_the_period_it_covers(self, make_window):
        assert (
            assemble.report_id_for(make_window()) == "macro_monthly_2026_05_01"
        )

    def test_a_week_and_a_month_starting_together_get_different_names(
        self, make_window
    ):
        from datetime import datetime, timezone

        week = make_window(
            ReportType.WEEKLY,
            start=datetime(2026, 6, 1, tzinfo=timezone.utc),
            end=datetime(2026, 6, 8, tzinfo=timezone.utc),
        )
        month = make_window(
            ReportType.MONTHLY,
            start=datetime(2026, 6, 1, tzinfo=timezone.utc),
            end=datetime(2026, 7, 1, tzinfo=timezone.utc),
        )

        assert assemble.report_id_for(week) != assemble.report_id_for(month)

    def test_a_deliberate_rerun_gets_a_new_name_rather_than_the_old_one(
        self, make_window
    ):
        # Nothing here overwrites, so both survive and the newer is shown.
        assert (
            assemble.report_id_for(make_window(), existing=1)
            == "macro_monthly_2026_05_01_r2"
        )


class TestTheFinishedDocument:
    def test_the_period_and_its_coverage_are_stated(
        self, make_corpus, make_episode_facts
    ):
        facts = analytics.compute(
            make_corpus(episodes=[make_episode_facts("ep_1")]), config=MacroConfig()
        )

        node, episode_ids = assemble.build(facts, written(), model_used="fake")

        assert node.episodes_analyzed == 1
        assert episode_ids == ("ep_1",)
        assert node.report_content["window"]["window_type"] == "monthly"

    def test_every_piece_of_writing_read_is_returned_for_linking(
        self, make_corpus, make_episode_facts
    ):
        # Including the ones that produced nothing worth a line. A report that
        # claims a period without naming what it looked at is an assertion.
        facts = analytics.compute(
            make_corpus(
                episodes=[
                    make_episode_facts("ep_1", day=4),
                    make_episode_facts("ep_2", day=9),
                ]
            ),
            config=MacroConfig(),
        )

        _, episode_ids = assemble.build(facts, written(), model_used="fake")

        assert episode_ids == ("ep_1", "ep_2")

    def test_the_document_records_which_shape_it_is(
        self, make_corpus, make_episode_facts
    ):
        facts = analytics.compute(
            make_corpus(episodes=[make_episode_facts("ep_1")]), config=MacroConfig()
        )

        node, _ = assemble.build(facts, written(), model_used="fake")

        assert node.report_content["meta"]["report_schema_version"] == 1

    def test_sections_not_produced_yet_are_named_rather_than_missing(
        self, make_corpus, make_episode_facts
    ):
        # A reader finding no mood chart should be able to tell "not built"
        # from "this period held no feeling".
        facts = analytics.compute(
            make_corpus(episodes=[make_episode_facts("ep_1")]), config=MacroConfig()
        )

        node, _ = assemble.build(facts, written(), model_used="fake")

        assert "emotional_valence" in node.report_content["meta"]["deferred_sections"]

    def test_missing_wording_is_recorded_on_the_document(
        self, make_corpus, make_episode_facts
    ):
        facts = analytics.compute(
            make_corpus(episodes=[make_episode_facts("ep_1")]), config=MacroConfig()
        )

        node, _ = assemble.build(
            facts,
            written(status=NarrativeStatus.UNAVAILABLE),
            model_used="none",
        )

        assert node.report_content["meta"]["narrative_status"] == "UNAVAILABLE"
        assert node.report_content["headline"] == ""

    def test_a_period_cut_short_says_so_on_the_document(
        self, make_corpus, make_episode_facts
    ):
        facts = analytics.compute(
            make_corpus(episodes=[make_episode_facts("ep_1")], truncated=True),
            config=MacroConfig(),
        )

        node, _ = assemble.build(facts, written(), model_used="fake")

        assert node.report_content["meta"]["truncated"] is True


class TestJoiningSentencesToFigures:
    def test_a_persons_sentence_lands_beside_their_count(
        self, make_corpus, make_episode_facts, make_observation_facts
    ):
        observations = tuple(
            make_observation_facts(
                f"obs_{i}",
                observation_type=ObservationType.RELATIONAL_DYNAMIC,
                people=("Alex",),
                episode_id="ep_1",
            )
            for i in range(2)
        )
        facts = analytics.compute(
            make_corpus(
                episodes=[make_episode_facts("ep_1", observations=observations)]
            ),
            config=MacroConfig(),
        )
        draft = NarrativeDraft(
            relational_summaries=[
                RelationalSummary(person_ref="Alex", dynamic_summary="steadier lately")
            ]
        )

        node, _ = assemble.build(facts, written(draft), model_used="fake")

        entry = node.report_content["key_relational_dynamics"][0]
        assert entry["person_ref"] == "Alex"
        assert entry["dynamic_summary"] == "steadier lately"
        assert entry["observation_count"] == 2

    def test_a_place_takes_its_confidence_from_the_evidence_not_the_model(
        self, make_corpus, make_episode_facts, make_observation_facts
    ):
        # A model's own estimate of its confidence is a sentence about itself,
        # not a measurement of what is underneath it.
        observation = make_observation_facts(
            "obs_1",
            observation_type=ObservationType.ENVIRONMENTAL_DEPENDENCY,
            signal=SignalStrength.HIGH,
            episode_id="ep_1",
        )
        facts = analytics.compute(
            make_corpus(
                episodes=[make_episode_facts("ep_1", observations=(observation,))]
            ),
            config=MacroConfig(),
        )
        draft = NarrativeDraft(
            environment_groups=[
                EnvironmentGroup(
                    environment="The office",
                    dependency="focus",
                    observation_ids=["obs_1"],
                )
            ]
        )

        node, _ = assemble.build(facts, written(draft), model_used="fake")

        assert node.report_content["key_environmental_dependencies"][0][
            "confidence"
        ] == "high"

    def test_a_place_built_on_lighter_evidence_is_reported_as_such(
        self, make_corpus, make_episode_facts, make_observation_facts
    ):
        observation = make_observation_facts(
            "obs_1",
            observation_type=ObservationType.ENVIRONMENTAL_DEPENDENCY,
            signal=SignalStrength.STANDARD,
            episode_id="ep_1",
        )
        facts = analytics.compute(
            make_corpus(
                episodes=[make_episode_facts("ep_1", observations=(observation,))]
            ),
            config=MacroConfig(),
        )
        draft = NarrativeDraft(
            environment_groups=[
                EnvironmentGroup(environment="Home", observation_ids=["obs_1"])
            ]
        )

        node, _ = assemble.build(facts, written(draft), model_used="fake")

        assert node.report_content["key_environmental_dependencies"][0][
            "confidence"
        ] == "medium"

    def test_a_relationships_direction_comes_from_the_record_where_it_exists(
        self, make_corpus, make_episode_facts, make_observation_facts, make_link
    ):
        episodes, links = [], []
        for index in range(3):
            observation = make_observation_facts(
                f"obs_{index}",
                observation_type=ObservationType.RELATIONAL_DYNAMIC,
                people=("Alex",),
                episode_id=f"ep_{index}",
            )
            episodes.append(
                make_episode_facts(f"ep_{index}", day=index + 1, observations=(observation,))
            )
            links.append(
                make_link(
                    observation.node_id,
                    "person_alex",
                    to_type="person",
                    edge_name="mentions_obs",
                )
            )

        facts = analytics.compute(
            make_corpus(
                episodes=episodes,
                links=links,
                people={
                    "person_alex": {
                        "node_id": "person_alex",
                        "canonical_name": "Alex",
                        "relationship_sentiment_trend": "NEGATIVE",
                    }
                },
            ),
            config=MacroConfig(),
        )
        draft = NarrativeDraft(
            relationship_arcs=[
                ArcNarrative(
                    person_id="person_alex",
                    arc_summary="warmer than before",
                    arc_direction=ArcDirection.STRENGTHENING,
                )
            ]
        )

        node, _ = assemble.build(facts, written(draft), model_used="fake")

        entry = node.report_content["relationship_arcs"][0]
        # The stored trend was built from every mention across all time; the
        # model read one period.
        assert entry["arc_direction"] == "STRAINING"
        assert entry["arc_summary"] == "warmer than before"

    def test_a_gap_defaults_to_still_missing_when_the_model_said_nothing(
        self, make_corpus, make_episode_facts, make_observation_facts
    ):
        observation = make_observation_facts(
            "obs_gap",
            observation_type=ObservationType.BIOGRAPHICAL_GAP,
            episode_id="ep_1",
        )
        facts = analytics.compute(
            make_corpus(
                episodes=[make_episode_facts("ep_1", observations=(observation,))]
            ),
            config=MacroConfig(),
        )

        node, _ = assemble.build(facts, written(), model_used="fake")

        assert node.report_content["biographical_gaps_raised"][0]["status"] == "present"

    def test_a_gap_the_model_judged_carries_that_judgement(
        self, make_corpus, make_episode_facts, make_observation_facts
    ):
        observation = make_observation_facts(
            "obs_gap",
            observation_type=ObservationType.BIOGRAPHICAL_GAP,
            episode_id="ep_1",
        )
        facts = analytics.compute(
            make_corpus(
                episodes=[make_episode_facts("ep_1", observations=(observation,))]
            ),
            config=MacroConfig(),
        )
        draft = NarrativeDraft(
            biographical_gaps=[
                GapJudgement(
                    observation_id="obs_gap",
                    status=GapStatus.NARROWING,
                    closing_evidence="Alex is the first instance",
                )
            ]
        )

        node, _ = assemble.build(facts, written(draft), model_used="fake")

        assert node.report_content["biographical_gaps_raised"][0]["status"] == "narrowing"

    def test_a_tension_carries_the_question_it_was_given(
        self, make_corpus, make_episode_facts
    ):
        facts = analytics.compute(
            make_corpus(
                episodes=[make_episode_facts("ep_1")],
                contradictions=[
                    {
                        "node_id": "con_1",
                        "belief_a_id": "bel_1",
                        "belief_b_id": "bel_2",
                        "resolution_status": "UNRESOLVED",
                        "valid_from": "2026-05-22T00:00:00+00:00",
                    }
                ],
            ),
            config=MacroConfig(),
        )
        draft = NarrativeDraft(
            contradiction_prompts=[
                ContradictionPrompt(
                    contradiction_id="con_1", reflection_prompt="Which is the fear?"
                )
            ]
        )

        node, _ = assemble.build(facts, written(draft), model_used="fake")

        assert (
            node.report_content["active_contradictions"][0]["reflection_prompt"]
            == "Which is the fear?"
        )


class TestTheMostConsequentialLine:
    def test_a_shift_is_named_only_when_the_arithmetic_found_one(
        self, make_corpus, make_episode_facts, make_window
    ):
        facts = analytics.compute(
            make_corpus(
                window=make_window(ReportType.QUARTERLY),
                episodes=[make_episode_facts("ep_1")],
            ),
            config=MacroConfig(),
        )
        draft = NarrativeDraft(
            archetype_shift=ArchetypeNarrative(shift_label="Fear → Freedom")
        )

        node, _ = assemble.build(facts, written(draft), model_used="fake")

        assert node.archetype_shift_detected is False
        assert node.report_content["archetype_shift"]["shift_label"] is None

    def test_a_real_shift_carries_both_the_name_and_the_patterns(
        self, make_corpus, make_window, make_episode_facts, pattern_row
    ):
        facts = analytics.compute(
            make_corpus(
                window=make_window(ReportType.QUARTERLY),
                episodes=[make_episode_facts("ep_1")],
                comparison_counts={f"pat_{i}": 4 for i in range(5)},
                all_patterns=[pattern_row(f"pat_{i}") for i in range(5)],
            ),
            config=MacroConfig(),
        )
        draft = NarrativeDraft(
            archetype_shift=ArchetypeNarrative(
                shift_label="Approval-seeking → Internal reference",
                evidence_summary="Five habits loosened together.",
            )
        )

        node, _ = assemble.build(facts, written(draft), model_used="fake")

        assert node.archetype_shift_detected is True
        assert (
            node.report_content["archetype_shift"]["shift_label"]
            == "Approval-seeking → Internal reference"
        )
        assert len(node.report_content["archetype_shift"]["contributing_patterns"]) == 5


class TestAnAlertAboutTheLastTwoDays:
    def test_it_records_what_was_seen_and_what_it_was_called(self, make_window):
        window = make_window(ReportType.SHADOW)
        finding = ShadowFinding(
            detected=True,
            trigger_nodes=("d_1", "d_2", "d_3"),
            episode_ids=("ep_1",),
            branch_count=3,
            target_count=3,
        )

        node, episode_ids = assemble.build_shadow(
            window,
            finding,
            ShadowNarrative(shift_type="Opening up", summary="Several things moved."),
            model_used="fake-light",
        )

        assert node.report_type is ReportType.SHADOW
        assert node.report_content["shadow_micro_shift"]["detected"] is True
        assert node.report_content["shadow_micro_shift"]["trigger_nodes"] == [
            "d_1",
            "d_2",
            "d_3",
        ]
        assert episode_ids == ("ep_1",)

    def test_two_alerts_in_one_week_do_not_collide(self, make_window):
        from datetime import datetime, timezone

        first = make_window(
            ReportType.SHADOW,
            start=datetime(2026, 5, 18, 9, tzinfo=timezone.utc),
            end=datetime(2026, 5, 20, 9, tzinfo=timezone.utc),
        )
        second = make_window(
            ReportType.SHADOW,
            start=datetime(2026, 5, 20, 9, tzinfo=timezone.utc),
            end=datetime(2026, 5, 22, 9, tzinfo=timezone.utc),
        )

        one, _ = assemble.build_shadow(
            first, ShadowFinding(detected=True), ShadowNarrative(), model_used="fake"
        )
        two, _ = assemble.build_shadow(
            second, ShadowFinding(detected=True), ShadowNarrative(), model_used="fake"
        )

        assert one.node_id != two.node_id


class TestProgressAndQuietPatterns:
    def test_the_improving_pattern_takes_the_wording_it_was_given(
        self, make_corpus, make_episode_facts, make_observation_facts, make_link, pattern_row
    ):
        observation = make_observation_facts("obs_1", episode_id="ep_1")
        facts = analytics.compute(
            make_corpus(
                episodes=[make_episode_facts("ep_1", observations=(observation,))],
                links=[make_link("obs_1", "pat_a", edge_name="regulates_obs")],
                patterns={"pat_a": pattern_row("pat_a", name="Comparison")},
                previous_pattern_episodes={"pat_a": 6},
            ),
            config=MacroConfig(),
        )
        draft = NarrativeDraft(
            growth_area_label="Letting other people's pace be theirs",
            growth_area_evidence="Interrupted deliberately, three times.",
        )

        node, _ = assemble.build(facts, written(draft), model_used="fake")

        growth = node.report_content["biggest_growth_area"]
        assert growth["pattern_or_belief_id"] == "pat_a"
        assert growth["label"] == "Letting other people's pace be theirs"
        assert growth["previous_episode_count"] == 6

    def test_the_improving_pattern_falls_back_to_its_own_name(
        self, make_corpus, make_episode_facts, make_observation_facts, make_link, pattern_row
    ):
        observation = make_observation_facts("obs_1", episode_id="ep_1")
        facts = analytics.compute(
            make_corpus(
                episodes=[make_episode_facts("ep_1", observations=(observation,))],
                links=[make_link("obs_1", "pat_a", edge_name="regulates_obs")],
                patterns={"pat_a": pattern_row("pat_a", name="Comparison")},
                previous_pattern_episodes={"pat_a": 6},
            ),
            config=MacroConfig(),
        )

        node, _ = assemble.build(facts, written(), model_used="fake")

        assert node.report_content["biggest_growth_area"]["label"] == "Comparison"

    def test_a_period_with_no_progress_says_nothing_rather_than_guessing(
        self, make_corpus, make_episode_facts
    ):
        facts = analytics.compute(
            make_corpus(episodes=[make_episode_facts("ep_1")]), config=MacroConfig()
        )

        node, _ = assemble.build(facts, written(), model_used="fake")

        assert node.report_content["biggest_growth_area"] is None
        assert node.report_content["biggest_struggle"] is None

    def test_quiet_patterns_are_split_by_how_quiet(
        self, make_corpus, make_episode_facts, pattern_row
    ):
        facts = analytics.compute(
            make_corpus(
                episodes=[make_episode_facts("ep_1")],
                all_patterns=[
                    pattern_row(
                        "pat_cooling", last_reinforced="2025-10-01T00:00:00+00:00"
                    ),
                    pattern_row(
                        "pat_dormant", last_reinforced="2024-01-01T00:00:00+00:00"
                    ),
                ],
            ),
            config=MacroConfig(),
        )

        node, _ = assemble.build(facts, written(), model_used="fake")

        aging = node.report_content["pattern_aging"]
        assert [item["pattern_id"] for item in aging["cooling_patterns"]] == [
            "pat_cooling"
        ]
        assert [item["pattern_id"] for item in aging["dormant_patterns"]] == [
            "pat_dormant"
        ]

    def test_only_a_dormant_pattern_carries_a_question(
        self, make_corpus, make_episode_facts, pattern_row
    ):
        facts = analytics.compute(
            make_corpus(
                episodes=[make_episode_facts("ep_1")],
                all_patterns=[
                    pattern_row(
                        "pat_cooling", last_reinforced="2025-10-01T00:00:00+00:00"
                    ),
                    pattern_row(
                        "pat_dormant", last_reinforced="2024-01-01T00:00:00+00:00"
                    ),
                ],
            ),
            config=MacroConfig(),
        )

        node, _ = assemble.build(facts, written(), model_used="fake")

        aging = node.report_content["pattern_aging"]
        assert "re_interrogation_prompt" not in aging["cooling_patterns"][0]
        assert "re_interrogation_prompt" in aging["dormant_patterns"][0]
