"""
Pass B — following anchors during a live conversation.

Run against a real graph, because every question this pass asks is a query
against typed edge tables and a stand-in would agree with whatever the test
author imagined.

The case that matters most is the one the Master Plan names: a turn that
refers to a period of somebody's life should surface what is filed under
that period, even when the words share nothing with what is stored.
"""

from __future__ import annotations

import pytest

from lumen.config import QueryConfig
from lumen.query.retrieval import structural
from lumen.schemas.enums import Domain, StructuralAnchorType, TriggerType


@pytest.fixture
def anchors(graph_store):
    """Run the anchor lookups for a set of reasons against the seeded graph."""

    def _find(*triggers, **settings):
        return structural.find_by_anchors(
            tuple(triggers), graph=graph_store, config=QueryConfig(**settings)
        )

    return _find


class TestAPeriodOfLife:
    def test_records_filed_under_that_period_come_back(
        self, anchors, seed_pattern, make_trigger
    ):
        # The Master Plan's named test for this goal.
        seed_pattern("pat_school", era_tag="high school years")

        found = anchors(
            make_trigger(TriggerType.HISTORICAL_ERA, era="high school years")
        )

        assert [node.node_id for node in found] == ["pat_school"]

    def test_the_period_is_recorded_on_what_it_found(
        self, anchors, seed_pattern, make_trigger
    ):
        # Knowing a record surfaced because a period matched — rather than
        # because it read similarly — changes how much to trust it.
        seed_pattern("pat_school", era_tag="high school years")

        found = anchors(
            make_trigger(TriggerType.HISTORICAL_ERA, era="high school years")
        )

        assert found[0].anchor_type is StructuralAnchorType.HISTORICAL_ERA
        assert found[0].anchor_value == "high school years"
        assert found[0].trigger_type is TriggerType.HISTORICAL_ERA

    def test_a_different_period_finds_nothing(
        self, anchors, seed_pattern, make_trigger
    ):
        seed_pattern("pat_school", era_tag="high school years")

        assert anchors(make_trigger(TriggerType.HISTORICAL_ERA, era="hostel")) == []

    def test_a_reason_naming_no_period_does_nothing(self, anchors, make_trigger):
        assert anchors(make_trigger(TriggerType.HISTORICAL_ERA, era=None)) == []

    def test_a_found_record_carries_no_measured_closeness(
        self, anchors, seed_pattern, make_trigger
    ):
        # An anchor match is not a measurement, and giving it a number would
        # invite somebody to compare the two as though it were.
        seed_pattern("pat_school", era_tag="high school years")

        found = anchors(
            make_trigger(TriggerType.HISTORICAL_ERA, era="high school years")
        )

        assert found[0].similarity is None
        assert found[0].rank_score > 0


class TestAPersonNamed:
    def test_notes_mentioning_them_come_back(
        self, anchors, graph_store, seed_person, seed_observation, make_trigger
    ):
        person_id = seed_person("Alex")
        seed_observation("obs_alex", "the conversation with Alex went badly")
        graph_store.write_edge("mentions_obs", "obs_alex", person_id)

        found = anchors(
            make_trigger(TriggerType.NAMED_PERSON, person_node_ids=(person_id,))
        )

        assert [node.node_id for node in found] == ["obs_alex"]
        assert found[0].anchor_value == "Alex"

    def test_the_standing_pattern_those_notes_became_comes_back_too(
        self,
        anchors,
        graph_store,
        seed_person,
        seed_observation,
        seed_pattern,
        make_trigger,
    ):
        # "What do I know about Alex" means the same thing whether the answer
        # is a note from Tuesday or the pattern that grew out of it. Without
        # the second hop, a person named again months later surfaces only the
        # individual notes.
        person_id = seed_person("Alex")
        seed_observation("obs_alex", "the conversation with Alex went badly")
        seed_pattern("pat_alex", name="Deferring to Alex")
        graph_store.write_edge("mentions_obs", "obs_alex", person_id)
        graph_store.write_edge("branches_to_obs_pat", "obs_alex", "pat_alex")

        found = anchors(
            make_trigger(TriggerType.NAMED_PERSON, person_node_ids=(person_id,))
        )

        assert {node.node_id for node in found} == {"obs_alex", "pat_alex"}

    def test_a_person_with_no_record_is_skipped(self, anchors, make_trigger):
        found = anchors(
            make_trigger(TriggerType.NAMED_PERSON, person_node_ids=("person_ghost",))
        )

        assert found == []

    def test_several_people_are_each_followed(
        self, anchors, graph_store, seed_person, seed_observation, make_trigger
    ):
        alex = seed_person("Alex")
        priya = seed_person("Priya")
        seed_observation("obs_a", "about Alex")
        seed_observation("obs_p", "about Priya")
        graph_store.write_edge("mentions_obs", "obs_a", alex)
        graph_store.write_edge("mentions_obs", "obs_p", priya)

        found = anchors(
            make_trigger(TriggerType.NAMED_PERSON, person_node_ids=(alex, priya))
        )

        assert {node.node_id for node in found} == {"obs_a", "obs_p"}


class TestUnfinishedQuestions:
    def test_open_questions_come_back(self, anchors, seed_open_loop, make_trigger):
        seed_open_loop("loop_1")

        found = anchors(make_trigger(TriggerType.OPEN_LOOP_MATCH))

        assert [node.node_id for node in found] == ["loop_1"]

    def test_a_claim_of_change_asks_for_them_too(
        self, anchors, seed_open_loop, make_trigger
    ):
        # Whether something has actually closed is a question about what was
        # left open.
        seed_open_loop("loop_1")

        found = anchors(make_trigger(TriggerType.PROGRESS_CLAIM))

        assert "loop_1" in {node.node_id for node in found}


class TestStandingRecords:
    def test_a_claim_of_change_also_asks_what_it_would_be_about(
        self, anchors, seed_pattern, make_trigger
    ):
        seed_pattern("pat_1")

        found = anchors(
            make_trigger(TriggerType.PROGRESS_CLAIM, domain=Domain.EMOTIONAL)
        )

        assert "pat_1" in {node.node_id for node in found}

    def test_questioning_a_belief_asks_for_the_beliefs_held(
        self, anchors, seed_belief, seed_pattern, make_trigger
    ):
        seed_belief("bel_1")
        seed_pattern("pat_1")

        found = anchors(
            make_trigger(TriggerType.BELIEF_CHALLENGE, domain=Domain.SELF_CONCEPT)
        )

        # Beliefs only. A pattern is a description of behaviour, not
        # something somebody holds to be true and can therefore doubt.
        assert [node.node_id for node in found] == ["bel_1"]

    def test_an_area_of_life_narrows_the_answer(
        self, anchors, seed_belief, make_trigger
    ):
        seed_belief("bel_self")

        found = anchors(
            make_trigger(TriggerType.BELIEF_CHALLENGE, domain=Domain.CAREER)
        )

        assert found == []

    def test_without_an_area_the_whole_live_self_model_is_offered(
        self, anchors, seed_belief, make_trigger
    ):
        seed_belief("bel_self")

        found = anchors(make_trigger(TriggerType.BELIEF_CHALLENGE, domain=None))

        assert [node.node_id for node in found] == ["bel_self"]

    def test_superseded_records_are_left_out(
        self, anchors, seed_pattern, make_trigger
    ):
        seed_pattern("pat_old", status="SUPERSEDED")

        found = anchors(
            make_trigger(TriggerType.PROGRESS_CLAIM, domain=Domain.EMOTIONAL)
        )

        assert found == []


class TestReasonsWithNoAnchor:
    @pytest.mark.parametrize(
        "trigger_type",
        [
            TriggerType.PATTERN_MENTION,
            TriggerType.SOMATIC_MARKER,
            TriggerType.IDENTITY_STATEMENT,
        ],
    )
    def test_they_contribute_nothing_here(
        self, anchors, seed_pattern, make_trigger, trigger_type
    ):
        # Not an oversight: a recurring feeling, a physical sensation and a
        # statement about who somebody is are not attached to anything the
        # graph can be asked for directly. The meaning-based search answers
        # them.
        seed_pattern("pat_1")

        assert anchors(make_trigger(trigger_type)) == []

    def test_every_reason_worth_looking_up_has_a_row(self):
        # A table rather than a chain of conditions, so a new reason is a
        # missing row rather than something quietly falling through.
        covered = set(structural.LOOKUPS)
        expected = set(TriggerType) - {TriggerType.NO_TRIGGER}

        assert covered == expected


class TestWhenTheGraphMisbehaves:
    def test_one_broken_lookup_does_not_cost_the_others(
        self, graph_store, seed_pattern, seed_open_loop, make_trigger, monkeypatch
    ):
        # Anchors are additive. Losing one should cost one.
        seed_open_loop("loop_1")

        def broken(*args, **kwargs):
            raise RuntimeError("the store said no")

        monkeypatch.setattr(graph_store, "find_nodes", broken)

        found = structural.find_by_anchors(
            (
                make_trigger(TriggerType.HISTORICAL_ERA, era="hostel"),
                make_trigger(TriggerType.OPEN_LOOP_MATCH),
            ),
            graph=graph_store,
            config=QueryConfig(),
        )

        assert found == []

    def test_an_unreadable_person_record_is_treated_as_unknown(
        self, graph_store, make_trigger, monkeypatch
    ):
        def broken(*args, **kwargs):
            raise RuntimeError("the store said no")

        monkeypatch.setattr(graph_store, "get_node", broken)

        found = structural.find_by_anchors(
            (make_trigger(TriggerType.NAMED_PERSON, person_node_ids=("person_x",)),),
            graph=graph_store,
            config=QueryConfig(),
        )

        assert found == []

    def test_a_person_record_with_no_name_is_skipped(
        self, graph_store, make_trigger, monkeypatch
    ):
        monkeypatch.setattr(graph_store, "get_node", lambda node_id: {"node_id": node_id})

        found = structural.find_by_anchors(
            (make_trigger(TriggerType.NAMED_PERSON, person_node_ids=("person_x",)),),
            graph=graph_store,
            config=QueryConfig(),
        )

        assert found == []


class TestHowMuchComesBack:
    def test_each_anchor_is_held_to_its_limit(
        self, anchors, seed_open_loop, make_trigger
    ):
        for index in range(4):
            seed_open_loop(f"loop_{index}")

        found = anchors(
            make_trigger(TriggerType.OPEN_LOOP_MATCH), conversational_pass_b_keep=2
        )

        assert len(found) == 2

    def test_no_reasons_means_no_lookups(self, anchors, seed_pattern):
        seed_pattern("pat_1")

        assert anchors() == []
