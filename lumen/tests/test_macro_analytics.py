"""
Tests for every number a periodic report contains.

These are the tests that matter most in the package, and they are the reason
the arithmetic was kept apart from the reading and the writing. Each one hands
in a small corpus built by hand and checks a figure that can be counted by eye,
with no database and no model anywhere near it.

The recurring theme is the difference between what the record says and what it
means. A pattern absent from a month may have resolved or may just not have
come up; a lesson unmentioned for six weeks may be settled or may be forgotten.
The report is expected to state what it can count and to stop there.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from lumen.config import MacroConfig
from lumen.pipeline.macroextraction import analytics
from lumen.schemas.enums import ObservationType, ReportType, SignalStrength

UTC = timezone.utc


class TestHowOftenPatternsFired:
    def test_a_pattern_is_counted_once_per_piece_of_writing(
        self, make_corpus, make_episode_facts, make_observation_facts, make_link, pattern_row
    ):
        # One entry that circles the same thing three times is one occasion of
        # it happening. Counting the mentions would make a talkative evening
        # look like a month.
        observations = tuple(
            make_observation_facts(f"obs_{i}", episode_id="ep_1") for i in range(3)
        )
        corpus = make_corpus(
            episodes=[make_episode_facts("ep_1", observations=observations)],
            links=[make_link(item.node_id, "pat_a") for item in observations],
            patterns={"pat_a": pattern_row("pat_a")},
        )

        counts = analytics.pattern_frequency(analytics.WindowIndex(corpus))

        assert [(item.pattern_id, item.episode_count) for item in counts] == [("pat_a", 1)]

    def test_frequency_is_a_share_of_the_period(
        self, make_corpus, make_episode_facts, make_observation_facts, make_link, pattern_row
    ):
        # A share rather than a count, so a seven-day week and a thirty-day
        # month can be held up against each other at all.
        episodes = []
        links = []
        for index in range(4):
            observation = make_observation_facts(f"obs_{index}", episode_id=f"ep_{index}")
            episodes.append(
                make_episode_facts(f"ep_{index}", day=index + 1, observations=(observation,))
            )
            if index < 3:
                links.append(make_link(observation.node_id, "pat_a"))

        corpus = make_corpus(
            episodes=episodes, links=links, patterns={"pat_a": pattern_row("pat_a")}
        )

        counts = analytics.pattern_frequency(analytics.WindowIndex(corpus))

        assert counts[0].frequency_pct == 75.0

    def test_the_first_and_last_days_it_appeared_are_reported(
        self, make_corpus, make_episode_facts, make_observation_facts, make_link, pattern_row
    ):
        first = make_observation_facts("obs_1", episode_id="ep_1")
        last = make_observation_facts("obs_2", episode_id="ep_2")
        corpus = make_corpus(
            episodes=[
                make_episode_facts("ep_1", day=4, observations=(first,)),
                make_episode_facts("ep_2", day=27, observations=(last,)),
            ],
            links=[make_link("obs_1", "pat_a"), make_link("obs_2", "pat_a")],
            patterns={"pat_a": pattern_row("pat_a")},
        )

        counts = analytics.pattern_frequency(analytics.WindowIndex(corpus))

        assert counts[0].first_seen == date(2026, 5, 4)
        assert counts[0].last_seen == date(2026, 5, 27)

    def test_a_link_from_writing_outside_the_period_is_ignored(
        self, make_corpus, make_episode_facts, make_observation_facts, make_link, pattern_row
    ):
        observation = make_observation_facts("obs_1", episode_id="ep_1")
        corpus = make_corpus(
            episodes=[make_episode_facts("ep_1", observations=(observation,))],
            links=[make_link("obs_from_last_year", "pat_a")],
            patterns={"pat_a": pattern_row("pat_a")},
        )

        assert analytics.pattern_frequency(analytics.WindowIndex(corpus)) == []

    def test_a_pattern_whose_record_is_missing_keeps_its_identifier(
        self, make_corpus, make_episode_facts, make_observation_facts, make_link
    ):
        observation = make_observation_facts("obs_1", episode_id="ep_1")
        corpus = make_corpus(
            episodes=[make_episode_facts("ep_1", observations=(observation,))],
            links=[make_link("obs_1", "pat_unknown")],
        )

        counts = analytics.pattern_frequency(analytics.WindowIndex(corpus))

        assert counts[0].label == "pat_unknown"

    def test_an_empty_period_reports_no_patterns(self, make_corpus):
        assert analytics.pattern_frequency(analytics.WindowIndex(make_corpus())) == []


class TestPatternsArrivingAndLeaving:
    def test_a_first_version_dated_inside_the_period_is_new(
        self, make_corpus, make_episode_facts, make_observation_facts, make_link, pattern_row
    ):
        observation = make_observation_facts("obs_1", episode_id="ep_1")
        corpus = make_corpus(
            episodes=[make_episode_facts("ep_1", observations=(observation,))],
            links=[make_link("obs_1", "pat_new")],
            patterns={"pat_new": pattern_row("pat_new")},
        )

        emerging = analytics.emerging_patterns(analytics.WindowIndex(corpus))

        assert [item.pattern_id for item in emerging] == ["pat_new"]
        assert emerging[0].first_episode == "ep_1"

    def test_a_pattern_that_predates_the_period_is_not_new(
        self, make_corpus, make_episode_facts, make_observation_facts, make_link, pattern_row
    ):
        observation = make_observation_facts("obs_1", episode_id="ep_1")
        corpus = make_corpus(
            episodes=[make_episode_facts("ep_1", observations=(observation,))],
            links=[make_link("obs_1", "pat_old")],
            patterns={
                "pat_old": pattern_row("pat_old", valid_from="2025-01-01T00:00:00+00:00")
            },
        )

        assert analytics.emerging_patterns(analytics.WindowIndex(corpus)) == []

    def test_a_later_version_is_not_new_even_when_written_inside_the_period(
        self, make_corpus, make_episode_facts, make_observation_facts, make_link, pattern_row
    ):
        # A second version is the same idea taking a new shape, which is a
        # different line in the report and already has one.
        observation = make_observation_facts("obs_1", episode_id="ep_1")
        corpus = make_corpus(
            episodes=[make_episode_facts("ep_1", observations=(observation,))],
            links=[make_link("obs_1", "pat_v2")],
            patterns={"pat_v2": pattern_row("pat_v2", version=2)},
        )

        assert analytics.emerging_patterns(analytics.WindowIndex(corpus)) == []

    def test_a_pattern_that_was_firing_and_stopped_is_reported(
        self, make_corpus, pattern_row
    ):
        corpus = make_corpus(
            previous_pattern_frequency={"pat_gone": 18.0},
            all_patterns=[pattern_row("pat_gone", name="Procrastination")],
        )

        gone = analytics.disappearing_patterns(analytics.WindowIndex(corpus))

        assert [(item.pattern_id, item.previous_frequency_pct) for item in gone] == [
            ("pat_gone", 18.0)
        ]

    def test_a_pattern_still_firing_is_not_reported_as_gone(
        self, make_corpus, make_episode_facts, make_observation_facts, make_link, pattern_row
    ):
        observation = make_observation_facts("obs_1", episode_id="ep_1")
        corpus = make_corpus(
            episodes=[make_episode_facts("ep_1", observations=(observation,))],
            links=[make_link("obs_1", "pat_a")],
            patterns={"pat_a": pattern_row("pat_a")},
            previous_pattern_frequency={"pat_a": 20.0},
            all_patterns=[pattern_row("pat_a")],
        )

        assert analytics.disappearing_patterns(analytics.WindowIndex(corpus)) == []


class TestBeliefsChangingShape:
    def test_a_revision_reports_both_wordings(self, make_corpus):
        corpus = make_corpus(
            decisions=[
                {
                    "node_id": "d_1",
                    "action": "EVOLVE",
                    "source_node_id": "bel_v2",
                    "target_node_id": "bel_v1",
                    "delta_description": "narrowed to low-energy states only",
                    "created_at": "2026-05-12T09:00:00+00:00",
                }
            ],
            beliefs={
                "bel_v1": {
                    "node_id": "bel_v1",
                    "version": 1,
                    "belief_statement": "Sleep is the only fix",
                },
                "bel_v2": {
                    "node_id": "bel_v2",
                    "version": 2,
                    "belief_statement": "Slow re-engagement works better",
                },
            },
        )

        changes = analytics.belief_changes(analytics.WindowIndex(corpus))

        assert changes[0].old_content == "Sleep is the only fix"
        assert changes[0].new_content == "Slow re-engagement works better"
        assert changes[0].delta_description == "narrowed to low-energy states only"

    def test_decisions_that_were_not_revisions_are_left_out(self, make_corpus):
        corpus = make_corpus(
            decisions=[
                {"node_id": "d_1", "action": "REINFORCE", "source_node_id": "bel_v1"}
            ],
            beliefs={"bel_v1": {"node_id": "bel_v1", "belief_statement": "x"}},
        )

        assert analytics.belief_changes(analytics.WindowIndex(corpus)) == []

    def test_a_revision_of_something_unread_is_skipped(self, make_corpus):
        corpus = make_corpus(
            decisions=[
                {
                    "node_id": "d_1",
                    "action": "EVOLVE",
                    "source_node_id": "pat_a",
                    "target_node_id": "pat_b",
                }
            ]
        )

        assert analytics.belief_changes(analytics.WindowIndex(corpus)) == []


class TestLessons:
    def test_a_lesson_reached_three_times_is_reported_as_repeated(
        self, make_corpus, make_episode_facts
    ):
        corpus = make_corpus(
            episodes=[make_episode_facts(f"ep_{i}", day=i + 1) for i in range(3)],
            lessons=[
                {
                    "node_id": "les_1",
                    "lesson_statement": "Environment beats willpower",
                    "evidence_episodes": ["ep_0", "ep_1", "ep_2"],
                }
            ],
        )

        repeated = analytics.repeated_lessons(
            analytics.WindowIndex(corpus), config=MacroConfig()
        )

        assert [(item.lesson_id, item.appearance_count) for item in repeated] == [
            ("les_1", 3)
        ]

    def test_a_lesson_reached_twice_is_below_the_bar(
        self, make_corpus, make_episode_facts
    ):
        corpus = make_corpus(
            episodes=[make_episode_facts(f"ep_{i}", day=i + 1) for i in range(3)],
            lessons=[
                {
                    "node_id": "les_1",
                    "lesson_statement": "x",
                    "evidence_episodes": ["ep_0", "ep_1"],
                }
            ],
        )

        assert (
            analytics.repeated_lessons(
                analytics.WindowIndex(corpus), config=MacroConfig()
            )
            == []
        )

    def test_evidence_from_outside_the_period_does_not_count(
        self, make_corpus, make_episode_facts
    ):
        corpus = make_corpus(
            episodes=[make_episode_facts("ep_0")],
            lessons=[
                {
                    "node_id": "les_1",
                    "lesson_statement": "x",
                    "evidence_episodes": ["ep_0", "ep_last_year", "ep_older"],
                }
            ],
        )

        assert (
            analytics.repeated_lessons(
                analytics.WindowIndex(corpus), config=MacroConfig()
            )
            == []
        )

    def test_a_lesson_untouched_for_weeks_is_reported_as_ignored(
        self, make_corpus, make_episode_facts
    ):
        corpus = make_corpus(
            episodes=[make_episode_facts("ep_1")],
            lessons=[
                {
                    "node_id": "les_old",
                    "lesson_statement": "Filter before asking",
                    "valid_from": "2026-04-10T00:00:00+00:00",
                    "evidence_episodes": ["ep_april"],
                }
            ],
        )

        ignored = analytics.ignored_lessons(
            analytics.WindowIndex(corpus), config=MacroConfig()
        )

        assert ignored[0].lesson_id == "les_old"
        assert ignored[0].days_since_last_seen == 52

    def test_a_lesson_reinforced_in_the_period_is_not_ignored(
        self, make_corpus, make_episode_facts
    ):
        corpus = make_corpus(
            episodes=[make_episode_facts("ep_1")],
            lessons=[
                {
                    "node_id": "les_1",
                    "lesson_statement": "x",
                    "valid_from": "2026-04-10T00:00:00+00:00",
                    "evidence_episodes": ["ep_1"],
                }
            ],
        )

        assert (
            analytics.ignored_lessons(
                analytics.WindowIndex(corpus), config=MacroConfig()
            )
            == []
        )

    def test_a_recent_lesson_is_not_yet_ignored(self, make_corpus, make_episode_facts):
        corpus = make_corpus(
            episodes=[make_episode_facts("ep_1")],
            lessons=[
                {
                    "node_id": "les_1",
                    "lesson_statement": "x",
                    "valid_from": "2026-05-29T00:00:00+00:00",
                    "evidence_episodes": [],
                }
            ],
        )

        assert (
            analytics.ignored_lessons(
                analytics.WindowIndex(corpus), config=MacroConfig()
            )
            == []
        )


class TestGrowthAndStruggle:
    def test_the_biggest_drop_that_was_worked_on_is_the_growth_candidate(
        self, make_corpus, make_episode_facts, make_observation_facts, make_link, pattern_row
    ):
        observation = make_observation_facts("obs_1", episode_id="ep_1")
        corpus = make_corpus(
            episodes=[make_episode_facts("ep_1", observations=(observation,))],
            links=[
                make_link("obs_1", "pat_worked_on", edge_name="regulates_obs"),
            ],
            patterns={"pat_worked_on": pattern_row("pat_worked_on")},
            previous_pattern_episodes={"pat_worked_on": 6},
        )

        candidate = analytics.growth_candidate(analytics.WindowIndex(corpus))

        assert candidate is not None
        assert candidate.node_id == "pat_worked_on"
        assert candidate.was_regulated is True

    def test_a_pattern_that_merely_went_quiet_is_not_growth(
        self, make_corpus, pattern_row
    ):
        # Something that stopped on its own is a different report line, and it
        # already has one. Calling it progress would be a claim the record
        # cannot support.
        corpus = make_corpus(
            previous_pattern_episodes={"pat_quiet": 6},
            all_patterns=[pattern_row("pat_quiet")],
        )

        assert analytics.growth_candidate(analytics.WindowIndex(corpus)) is None

    def test_a_revision_also_counts_as_having_been_worked_on(
        self, make_corpus, pattern_row
    ):
        corpus = make_corpus(
            previous_pattern_episodes={"pat_a": 5},
            patterns={"pat_a": pattern_row("pat_a")},
            decisions=[
                {"node_id": "d_1", "action": "EVOLVE", "target_node_id": "pat_a"}
            ],
        )

        candidate = analytics.growth_candidate(analytics.WindowIndex(corpus))

        assert candidate is not None
        assert candidate.was_evolved is True

    def test_the_most_frequent_pattern_is_the_struggle(
        self, make_corpus, make_episode_facts, make_observation_facts, make_link, pattern_row
    ):
        episodes, links = [], []
        for index in range(3):
            observation = make_observation_facts(
                f"obs_{index}",
                episode_id=f"ep_{index}",
                observation_type=ObservationType.ANTICIPATORY_ANXIETY,
            )
            episodes.append(
                make_episode_facts(f"ep_{index}", day=index + 1, observations=(observation,))
            )
            links.append(make_link(observation.node_id, "pat_anxiety"))

        corpus = make_corpus(
            episodes=episodes, links=links, patterns={"pat_anxiety": pattern_row("pat_anxiety")}
        )
        index = analytics.WindowIndex(corpus)

        candidate = analytics.struggle_candidate(
            index, analytics.pattern_frequency(index)
        )

        assert candidate is not None
        assert candidate.pattern_id == "pat_anxiety"
        # A count of difficult noticings written down, not a score of how the
        # period felt. Nothing in Lumen measures that.
        assert candidate.negative_observation_count == 3

    def test_untroubled_noticings_are_not_counted_as_difficulty(
        self, make_corpus, make_episode_facts, make_observation_facts, make_link, pattern_row
    ):
        observation = make_observation_facts(
            "obs_1",
            episode_id="ep_1",
            observation_type=ObservationType.GRATITUDE_APPRECIATION,
        )
        corpus = make_corpus(
            episodes=[make_episode_facts("ep_1", observations=(observation,))],
            links=[make_link("obs_1", "pat_a")],
            patterns={"pat_a": pattern_row("pat_a")},
        )
        index = analytics.WindowIndex(corpus)

        candidate = analytics.struggle_candidate(
            index, analytics.pattern_frequency(index)
        )

        assert candidate.negative_observation_count == 0

    def test_a_period_with_no_patterns_has_no_struggle(self, make_corpus):
        index = analytics.WindowIndex(make_corpus())

        assert analytics.struggle_candidate(index, []) is None


class TestPeopleAndPlaces:
    def test_a_person_named_twice_is_reported(
        self, make_corpus, make_episode_facts, make_observation_facts
    ):
        observations = tuple(
            make_observation_facts(
                f"obs_{i}",
                observation_type=ObservationType.RELATIONAL_DYNAMIC,
                content=f"talked to Alex again ({i})",
                people=("Alex",),
                episode_id="ep_1",
            )
            for i in range(2)
        )
        corpus = make_corpus(
            episodes=[make_episode_facts("ep_1", observations=observations)]
        )

        found = analytics.relational_dynamics(
            analytics.WindowIndex(corpus), config=MacroConfig()
        )

        assert [(item.person_ref, item.observation_count) for item in found] == [
            ("Alex", 2)
        ]

    def test_a_person_named_once_is_below_the_bar(
        self, make_corpus, make_episode_facts, make_observation_facts
    ):
        observation = make_observation_facts(
            "obs_1",
            observation_type=ObservationType.RELATIONAL_DYNAMIC,
            people=("Sam",),
            episode_id="ep_1",
        )
        corpus = make_corpus(
            episodes=[make_episode_facts("ep_1", observations=(observation,))]
        )

        assert (
            analytics.relational_dynamics(
                analytics.WindowIndex(corpus), config=MacroConfig()
            )
            == []
        )

    def test_environment_notes_are_handed_over_ungrouped(
        self, make_corpus, make_episode_facts, make_observation_facts
    ):
        # Grouping is not a job for code. "The office" and "my desk at work"
        # are one place and no rule will ever say so.
        observations = (
            make_observation_facts(
                "obs_1",
                observation_type=ObservationType.ENVIRONMENTAL_DEPENDENCY,
                content="only focus at the office",
                episode_id="ep_1",
            ),
            make_observation_facts(
                "obs_2",
                observation_type=ObservationType.ENVIRONMENTAL_DEPENDENCY,
                content="my desk at work is where it happens",
                episode_id="ep_1",
            ),
        )
        corpus = make_corpus(
            episodes=[make_episode_facts("ep_1", observations=observations)]
        )

        found = analytics.environment_observations(analytics.WindowIndex(corpus))

        assert [item.observation_id for item in found] == ["obs_1", "obs_2"]

    def test_a_relationship_needs_several_episodes_to_have_an_arc(
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

        corpus = make_corpus(
            episodes=episodes,
            links=links,
            people={
                "person_alex": {
                    "node_id": "person_alex",
                    "canonical_name": "Alex",
                    "relationship_sentiment_trend": "POSITIVE",
                }
            },
        )

        arcs = analytics.relationship_arcs(
            analytics.WindowIndex(corpus), config=MacroConfig()
        )

        assert arcs[0].person_id == "person_alex"
        assert arcs[0].episodes_in_window == 3
        # Taken from the person's own record where it says anything, because
        # that was built from every mention rather than from one period.
        assert arcs[0].stored_direction.value == "STRENGTHENING"

    def test_a_relationship_in_two_episodes_has_no_arc(
        self, make_corpus, make_episode_facts, make_observation_facts, make_link
    ):
        episodes, links = [], []
        for index in range(2):
            observation = make_observation_facts(f"obs_{index}", episode_id=f"ep_{index}")
            episodes.append(
                make_episode_facts(f"ep_{index}", day=index + 1, observations=(observation,))
            )
            links.append(
                make_link(
                    observation.node_id, "person_sam", to_type="person", edge_name="mentions_obs"
                )
            )

        corpus = make_corpus(episodes=episodes, links=links)

        assert (
            analytics.relationship_arcs(
                analytics.WindowIndex(corpus), config=MacroConfig()
            )
            == []
        )


class TestWhatIsStillOpen:
    def test_an_open_question_is_reported_with_how_long_it_has_been_open(
        self, make_corpus, make_episode_facts
    ):
        corpus = make_corpus(
            episodes=[make_episode_facts("ep_1")],
            open_loops=[
                {
                    "node_id": "loop_1",
                    "loop_description": "Why do I hesitate to ask questions?",
                    "resolution_status": "OPEN",
                    "valid_from": "2026-05-10T00:00:00+00:00",
                }
            ],
        )

        found = analytics.unresolved_open_loops(
            analytics.WindowIndex(corpus), config=MacroConfig()
        )

        assert found[0].open_loop_id == "loop_1"
        assert found[0].days_open == 22

    def test_a_question_the_period_settled_is_left_out(
        self, make_corpus, make_episode_facts
    ):
        # The writing that closes a question is better evidence than the field
        # on the question saying it is open.
        corpus = make_corpus(
            episodes=[make_episode_facts("ep_1")],
            open_loops=[
                {
                    "node_id": "loop_1",
                    "loop_description": "x",
                    "resolution_status": "OPEN",
                    "valid_from": "2026-05-10T00:00:00+00:00",
                }
            ],
            closed_loop_ids=("loop_1",),
        )

        assert (
            analytics.unresolved_open_loops(
                analytics.WindowIndex(corpus), config=MacroConfig()
            )
            == []
        )

    def test_an_already_resolved_question_is_left_out(
        self, make_corpus, make_episode_facts
    ):
        corpus = make_corpus(
            episodes=[make_episode_facts("ep_1")],
            open_loops=[
                {
                    "node_id": "loop_1",
                    "loop_description": "x",
                    "resolution_status": "RESOLVED",
                    "valid_from": "2026-05-10T00:00:00+00:00",
                }
            ],
        )

        assert (
            analytics.unresolved_open_loops(
                analytics.WindowIndex(corpus), config=MacroConfig()
            )
            == []
        )

    def test_an_unresolved_tension_reports_both_sides_in_the_persons_words(
        self, make_corpus, make_episode_facts
    ):
        corpus = make_corpus(
            episodes=[make_episode_facts("ep_1")],
            contradictions=[
                {
                    "node_id": "con_1",
                    "belief_a_id": "bel_1",
                    "belief_b_id": "bel_2",
                    "contradiction_summary": "introversion against thriving in crowds",
                    "resolution_status": "UNRESOLVED",
                    "valid_from": "2026-05-22T00:00:00+00:00",
                }
            ],
            beliefs={
                "bel_1": {"node_id": "bel_1", "belief_statement": "I am introverted"},
                "bel_2": {
                    "node_id": "bel_2",
                    "belief_statement": "I come alive in loud rooms",
                },
            },
        )

        found = analytics.active_contradictions(analytics.WindowIndex(corpus))

        assert found[0].belief_a == "I am introverted"
        assert found[0].belief_b == "I come alive in loud rooms"
        assert found[0].days_open == 10

    def test_a_tension_falls_back_to_identifiers_when_a_belief_was_not_read(
        self, make_corpus, make_episode_facts
    ):
        # A tension is worth reporting even when only one half can be quoted.
        corpus = make_corpus(
            episodes=[make_episode_facts("ep_1")],
            contradictions=[
                {
                    "node_id": "con_1",
                    "belief_a_id": "bel_1",
                    "belief_b_id": "bel_missing",
                    "resolution_status": "UNRESOLVED",
                    "valid_from": "2026-05-22T00:00:00+00:00",
                }
            ],
            beliefs={"bel_1": {"node_id": "bel_1", "belief_statement": "I am introverted"}},
        )

        found = analytics.active_contradictions(analytics.WindowIndex(corpus))

        assert found[0].belief_b == "bel_missing"

    def test_a_resolved_tension_is_left_out(self, make_corpus, make_episode_facts):
        corpus = make_corpus(
            episodes=[make_episode_facts("ep_1")],
            contradictions=[
                {
                    "node_id": "con_1",
                    "belief_a_id": "bel_1",
                    "belief_b_id": "bel_2",
                    "resolution_status": "RESOLVED",
                }
            ],
        )

        assert analytics.active_contradictions(analytics.WindowIndex(corpus)) == []


class TestWhatIsCarriedRegardless:
    def test_weighty_noticings_are_carried_whatever_else_the_period_held(
        self, make_corpus, make_episode_facts, make_observation_facts
    ):
        # Some things happen once and are still the most important thing that
        # happened. A report built purely on frequency would lose all of them.
        observations = (
            make_observation_facts("obs_1", signal=SignalStrength.CRITICAL),
            make_observation_facts("obs_2", signal=SignalStrength.STANDARD),
            make_observation_facts("obs_3", signal=SignalStrength.HIGH),
        )
        corpus = make_corpus(
            episodes=[make_episode_facts("ep_1", observations=observations)]
        )

        found = analytics.high_signal_observations(
            analytics.WindowIndex(corpus), config=MacroConfig()
        )

        assert [item.observation_id for item in found] == ["obs_1", "obs_3"]

    def test_the_list_is_capped(
        self, make_corpus, make_episode_facts, make_observation_facts
    ):
        observations = tuple(
            make_observation_facts(f"obs_{i}", signal=SignalStrength.HIGH)
            for i in range(10)
        )
        corpus = make_corpus(
            episodes=[make_episode_facts("ep_1", observations=observations)]
        )

        found = analytics.high_signal_observations(
            analytics.WindowIndex(corpus), config=MacroConfig(high_signal_limit=3)
        )

        assert len(found) == 3

    def test_patterns_reached_through_unbidden_feeling_are_reported_separately(
        self, make_corpus, make_episode_facts, make_observation_facts, make_link, pattern_row
    ):
        surfacing = make_observation_facts(
            "obs_surfacing",
            observation_type=ObservationType.SUPPRESSED_EMOTION_SURFACING,
            signal=SignalStrength.HIGH,
            episode_id="ep_1",
        )
        ordinary = make_observation_facts("obs_plain", episode_id="ep_1")
        corpus = make_corpus(
            episodes=[make_episode_facts("ep_1", observations=(surfacing, ordinary))],
            links=[
                make_link("obs_surfacing", "pat_mentorship"),
                make_link("obs_plain", "pat_other"),
            ],
            patterns={
                "pat_mentorship": pattern_row("pat_mentorship"),
                "pat_other": pattern_row("pat_other"),
            },
        )

        motifs = analytics.unprocessed_motifs(analytics.WindowIndex(corpus))

        assert [item.pattern_id for item in motifs] == ["pat_mentorship"]

    def test_nothing_surfacing_means_no_motifs(
        self, make_corpus, make_episode_facts, make_observation_facts
    ):
        corpus = make_corpus(
            episodes=[
                make_episode_facts(
                    "ep_1", observations=(make_observation_facts("obs_1"),)
                )
            ]
        )

        assert analytics.unprocessed_motifs(analytics.WindowIndex(corpus)) == []

    def test_gaps_in_the_story_are_reported_with_when_they_were_raised(
        self, make_corpus, make_episode_facts, make_observation_facts
    ):
        observation = make_observation_facts(
            "obs_gap",
            observation_type=ObservationType.BIOGRAPHICAL_GAP,
            content="no mentor figure across school or college",
            episode_id="ep_1",
        )
        corpus = make_corpus(
            episodes=[make_episode_facts("ep_1", day=18, observations=(observation,))]
        )

        gaps = analytics.biographical_gaps(analytics.WindowIndex(corpus))

        assert gaps[0].observation_id == "obs_gap"
        assert gaps[0].first_raised.date() == date(2026, 5, 18)


class TestPuttingTheWholeThingTogether:
    def test_every_section_is_produced_in_one_pass(
        self, make_corpus, make_episode_facts, make_observation_facts, make_link, pattern_row
    ):
        observation = make_observation_facts("obs_1", episode_id="ep_1")
        corpus = make_corpus(
            episodes=[make_episode_facts("ep_1", observations=(observation,))],
            links=[make_link("obs_1", "pat_a")],
            patterns={"pat_a": pattern_row("pat_a")},
            pending_review=(3, datetime(2026, 5, 20, tzinfo=UTC)),
        )

        facts = analytics.compute(corpus, config=MacroConfig())

        assert facts.episodes_analyzed == 1
        assert facts.episode_ids == ["ep_1"]
        assert facts.pending_review_count == 3
        assert facts.pending_review_oldest_days == 12

    def test_an_empty_period_computes_to_an_empty_report(self, make_corpus):
        facts = analytics.compute(make_corpus(), config=MacroConfig())

        assert facts.episodes_analyzed == 0
        assert facts.pattern_frequency == []
        assert facts.archetype_shift.detected is False

    def test_a_period_that_was_cut_short_says_so(self, make_corpus, make_episode_facts):
        corpus = make_corpus(episodes=[make_episode_facts("ep_1")], truncated=True)

        assert analytics.compute(corpus, config=MacroConfig()).truncated is True

    def test_nothing_pending_reports_no_oldest_item(
        self, make_corpus, make_episode_facts
    ):
        corpus = make_corpus(episodes=[make_episode_facts("ep_1")])

        facts = analytics.compute(corpus, config=MacroConfig())

        assert facts.pending_review_count == 0
        assert facts.pending_review_oldest_days is None

    def test_the_top_list_is_a_capped_view_of_the_full_one(
        self, make_corpus, make_episode_facts, make_observation_facts, make_link, pattern_row
    ):
        observations = tuple(
            make_observation_facts(f"obs_{i}", episode_id="ep_1") for i in range(5)
        )
        corpus = make_corpus(
            episodes=[make_episode_facts("ep_1", observations=observations)],
            links=[make_link(f"obs_{i}", f"pat_{i}") for i in range(5)],
            patterns={f"pat_{i}": pattern_row(f"pat_{i}") for i in range(5)},
        )

        facts = analytics.compute(corpus, config=MacroConfig(top_patterns_limit=2))

        assert len(facts.top_patterns) == 2
        assert len(facts.pattern_frequency) == 5

    def test_an_unreadable_timestamp_does_not_break_a_report(
        self, make_corpus, make_episode_facts
    ):
        corpus = make_corpus(
            episodes=[make_episode_facts("ep_1")],
            lessons=[
                {"node_id": "les_1", "lesson_statement": "x", "valid_from": "not a date"}
            ],
        )

        facts = analytics.compute(corpus, config=MacroConfig())

        assert facts.ignored_lessons == []


class TestTheIndexEverySectionShares:
    def test_beliefs_are_indexed_by_the_writing_they_appeared_in(
        self, make_corpus, make_episode_facts, make_observation_facts, make_link
    ):
        observation = make_observation_facts("obs_1", episode_id="ep_1")
        corpus = make_corpus(
            episodes=[make_episode_facts("ep_1", observations=(observation,))],
            links=[
                make_link(
                    "obs_1", "bel_1", to_type="belief", edge_name="reinforces_obs_bel"
                )
            ],
        )

        index = analytics.WindowIndex(corpus)

        assert index.belief_episodes["bel_1"] == {"ep_1"}

    def test_a_link_of_a_kind_nothing_reads_is_simply_ignored(
        self, make_corpus, make_episode_facts, make_observation_facts, make_link
    ):
        observation = make_observation_facts("obs_1", episode_id="ep_1")
        corpus = make_corpus(
            episodes=[make_episode_facts("ep_1", observations=(observation,))],
            links=[
                make_link(
                    "obs_1", "prin_1", to_type="principle", edge_name="adopted_as_obs"
                )
            ],
        )

        index = analytics.WindowIndex(corpus)

        assert index.pattern_episodes == {}
        assert index.belief_episodes == {}

    def test_a_share_of_an_empty_period_is_nothing_rather_than_an_error(
        self, make_corpus
    ):
        assert analytics.WindowIndex(make_corpus()).share(3) == 0.0

    def test_a_span_with_no_starting_point_is_nothing(self, make_corpus):
        index = analytics.WindowIndex(make_corpus())

        assert index.days_between(None, datetime(2026, 6, 1, tzinfo=UTC)) == 0

    def test_a_span_that_would_run_backwards_is_nothing(self, make_corpus):
        index = analytics.WindowIndex(make_corpus())

        assert (
            index.days_between(
                datetime(2026, 7, 1, tzinfo=UTC), datetime(2026, 6, 1, tzinfo=UTC)
            )
            == 0
        )


class TestWhenARecordWasNotRead:
    def test_a_new_pattern_whose_record_is_missing_is_not_called_new(
        self, make_corpus, make_episode_facts, make_observation_facts, make_link
    ):
        # Without the record there is no way to tell a first version from a
        # fifth, and guessing would put a years-old habit under "new".
        observation = make_observation_facts("obs_1", episode_id="ep_1")
        corpus = make_corpus(
            episodes=[make_episode_facts("ep_1", observations=(observation,))],
            links=[make_link("obs_1", "pat_unread")],
        )

        assert analytics.emerging_patterns(analytics.WindowIndex(corpus)) == []

    def test_a_pattern_that_stopped_but_was_never_read_is_not_reported(
        self, make_corpus
    ):
        corpus = make_corpus(previous_pattern_frequency={"pat_unread": 20.0})

        assert analytics.disappearing_patterns(analytics.WindowIndex(corpus)) == []

    def test_a_belief_with_no_version_recorded_reads_as_the_first(self, make_corpus):
        corpus = make_corpus(
            decisions=[
                {
                    "node_id": "d_1",
                    "action": "EVOLVE",
                    "source_node_id": "bel_v2",
                    "target_node_id": "bel_v1",
                }
            ],
            beliefs={
                "bel_v1": {"node_id": "bel_v1", "belief_statement": "old"},
                "bel_v2": {"node_id": "bel_v2", "version": 2, "belief_statement": "new"},
            },
        )

        changes = analytics.belief_changes(analytics.WindowIndex(corpus))

        assert changes[0].old_version == 1
        assert changes[0].new_version == 2
