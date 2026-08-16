"""
Checking a model's claims against what the graph really holds.

These run against a real embedded graph rather than a stand-in. Every
question here is a lookup, and a stand-in agrees with whatever the test
author imagined — including about things like how an era name is stored,
which is exactly what this code exists to get right.
"""

from __future__ import annotations

import pytest

from lumen.config import QueryConfig
from lumen.query.formulation.contracts import RawTrigger
from lumen.query.formulation.grounding import (
    CHECKS,
    GroundingContext,
    clean_names,
    era_vocabulary,
    ground_triggers,
    parse_domain,
)
from lumen.schemas.enums import Domain, TriggerType
from lumen.schemas.ids import person_node_id


@pytest.fixture
def seed_person(graph_store):
    """Put one person's record into the graph."""

    def _seed(name: str = "Alex") -> str:
        node_id = person_node_id(name)
        graph_store.write_node(
            "PersonEntityNode",
            {
                "node_id": node_id,
                "canonical_name": name,
                "first_mentioned_at": "2026-06-01T00:00:00+00:00",
                "last_mentioned_at": "2026-06-01T00:00:00+00:00",
                "mention_count": 1,
                "relationship_to_user": "FRIEND",
                "relationship_sentiment_trend": "STABLE",
                "is_canonical": True,
                "status": "ACTIVE",
                "aliases": "[]",
            },
        )
        return node_id

    return _seed


@pytest.fixture
def seed_open_loop(graph_store):
    """Put one unfinished question into the graph."""

    def _seed(node_id: str = "loop_001") -> str:
        graph_store.write_node(
            "OpenLoopNode",
            {
                "node_id": node_id,
                "created_at": "2026-06-01T00:00:00+00:00",
                "valid_from": "2026-06-01T00:00:00+00:00",
                "loop_description": "Is the resistance about leaving, or about being alone?",
                "loop_category": "DECISION_PENDING",
                "provenance": "USER_GENERATED",
                "source_episode_id": "ep_2026_06_01_001",
                "resolution_status": "OPEN",
            },
        )
        return node_id

    return _seed


def context_for(graph, *, eras=(), keyword_limit=6) -> GroundingContext:
    """A checking context over a given graph."""
    return GroundingContext(graph=graph, eras=tuple(eras), keyword_limit=keyword_limit)


class TestTheEraVocabulary:
    def test_it_reads_the_names_the_graph_really_uses(
        self, graph_store, chat_session, seed_pattern
    ):
        seed_pattern(node_id="pat_a", era_tag="high school years")

        assert era_vocabulary(graph_store, chat_session, config=QueryConfig()) == (
            "high school years",
        )

    def test_an_empty_graph_has_no_eras(self, graph_store, chat_session):
        assert era_vocabulary(graph_store, chat_session, config=QueryConfig()) == ()

    def test_it_is_read_once_a_day_rather_than_once_a_turn(
        self, graph_store, chat_session, seed_pattern
    ):
        seed_pattern(node_id="pat_a", era_tag="first job")
        era_vocabulary(graph_store, chat_session, config=QueryConfig())

        # Anything written afterwards is deliberately not picked up: the
        # pipeline cannot write while somebody is mid-conversation, and a
        # lookup per turn would be a database read on every message.
        seed_pattern(node_id="pat_b", era_tag="university")

        assert era_vocabulary(graph_store, chat_session, config=QueryConfig()) == (
            "first job",
        )

    def test_a_graph_that_cannot_answer_leaves_the_list_empty(self, chat_session):
        class Broken:
            def list_era_tags(self, *, limit=50):
                raise RuntimeError("the store is not answering")

        assert era_vocabulary(Broken(), chat_session, config=QueryConfig()) == ()

    def test_a_failed_read_is_not_retried_every_turn(self, chat_session):
        class CountingBroken:
            calls = 0

            def list_era_tags(self, *, limit=50):
                CountingBroken.calls += 1
                raise RuntimeError("still not answering")

        graph = CountingBroken()
        era_vocabulary(graph, chat_session, config=QueryConfig())
        era_vocabulary(graph, chat_session, config=QueryConfig())

        assert CountingBroken.calls == 1

    def test_blank_names_never_reach_the_vocabulary(self, chat_session):
        class Messy:
            def list_era_tags(self, *, limit=50):
                return ["high school", "   ", ""]

        assert era_vocabulary(Messy(), chat_session, config=QueryConfig()) == (
            "high school",
        )

    def test_how_many_are_offered_is_configurable(
        self, graph_store, chat_session, seed_pattern
    ):
        for index in range(4):
            seed_pattern(node_id=f"pat_{index}", era_tag=f"era {index}")

        names = era_vocabulary(
            graph_store, chat_session, config=QueryConfig(era_vocabulary_limit=2)
        )

        assert len(names) == 2


class TestNamingAPerson:
    def test_somebody_with_a_record_grounds_to_it(self, graph_store, seed_person):
        node_id = seed_person("Alex")

        kept = ground_triggers(
            [RawTrigger(trigger_type="NAMED_PERSON", people=["Alex"])],
            context=context_for(graph_store),
        )

        assert kept[0].person_node_ids == (node_id,)

    def test_somebody_the_graph_has_never_heard_of_is_dropped(self, graph_store):
        kept = ground_triggers(
            [RawTrigger(trigger_type="NAMED_PERSON", people=["Nobody"])],
            context=context_for(graph_store),
        )

        assert kept == ()

    def test_the_known_half_of_a_pair_survives(self, graph_store, seed_person):
        node_id = seed_person("Alex")

        kept = ground_triggers(
            [RawTrigger(trigger_type="NAMED_PERSON", people=["Alex", "Nobody"])],
            context=context_for(graph_store),
        )

        assert kept[0].person_node_ids == (node_id,)

    def test_a_name_written_differently_still_finds_the_record(
        self, graph_store, seed_person
    ):
        node_id = seed_person("Alex")

        kept = ground_triggers(
            [RawTrigger(trigger_type="NAMED_PERSON", people=["  alex  "])],
            context=context_for(graph_store),
        )

        assert kept[0].person_node_ids == (node_id,)

    def test_a_nickname_does_not_find_the_person_behind_it(
        self, graph_store, seed_person
    ):
        # An inherited limit, not a new one: the pipeline does not join two
        # spellings of one person either, and inventing half an answer here
        # would be worse than not answering.
        seed_person("Alex")

        kept = ground_triggers(
            [RawTrigger(trigger_type="NAMED_PERSON", people=["my brother"])],
            context=context_for(graph_store),
        )

        assert kept == ()

    def test_the_same_person_named_twice_is_offered_once(
        self, graph_store, seed_person
    ):
        seed_person("Alex")

        kept = ground_triggers(
            [RawTrigger(trigger_type="NAMED_PERSON", people=["Alex", "alex"])],
            context=context_for(graph_store),
        )

        assert len(kept[0].person_node_ids) == 1

    @pytest.mark.parametrize("name", ["", "   ", "!!!"])
    def test_a_name_that_cannot_become_an_identifier_is_dropped(
        self, graph_store, name
    ):
        kept = ground_triggers(
            [RawTrigger(trigger_type="NAMED_PERSON", people=[name])],
            context=context_for(graph_store),
        )

        assert kept == ()

    def test_a_graph_that_cannot_answer_drops_the_reason(self):
        class Broken:
            def get_node(self, node_id):
                raise RuntimeError("the store is not answering")

        kept = ground_triggers(
            [RawTrigger(trigger_type="NAMED_PERSON", people=["Alex"])],
            context=context_for(Broken()),
        )

        assert kept == ()


class TestNamingAnEra:
    def test_an_era_the_graph_holds_grounds_to_its_own_spelling(self, graph_store):
        kept = ground_triggers(
            [RawTrigger(trigger_type="HISTORICAL_ERA", era="HIGH SCHOOL")],
            context=context_for(graph_store, eras=["high school"]),
        )

        assert kept[0].era == "high school"

    @pytest.mark.parametrize(
        "said", ["HIGH_SCHOOL", "high-school", "High School", "  high school  "]
    )
    def test_spelling_differences_do_not_break_the_match(self, graph_store, said):
        kept = ground_triggers(
            [RawTrigger(trigger_type="HISTORICAL_ERA", era=said)],
            context=context_for(graph_store, eras=["high school"]),
        )

        assert kept[0].era == "high school"

    def test_an_era_this_history_does_not_use_is_dropped(self, graph_store):
        # The failure this prevents is silent: the lookup would run, match
        # nothing, and be indistinguishable from a period nothing was ever
        # written about.
        kept = ground_triggers(
            [RawTrigger(trigger_type="HISTORICAL_ERA", era="HIGH_SCHOOL")],
            context=context_for(graph_store, eras=["first job"]),
        )

        assert kept == ()

    def test_an_era_reason_with_no_era_on_it_is_dropped(self, graph_store):
        kept = ground_triggers(
            [RawTrigger(trigger_type="HISTORICAL_ERA")],
            context=context_for(graph_store, eras=["high school"]),
        )

        assert kept == ()

    def test_nothing_grounds_when_the_history_records_no_eras(self, graph_store):
        kept = ground_triggers(
            [RawTrigger(trigger_type="HISTORICAL_ERA", era="high school")],
            context=context_for(graph_store, eras=[]),
        )

        assert kept == ()


class TestUnfinishedQuestions:
    def test_the_reason_survives_when_something_is_open(
        self, graph_store, seed_open_loop
    ):
        seed_open_loop()

        kept = ground_triggers(
            [RawTrigger(trigger_type="OPEN_LOOP_MATCH")],
            context=context_for(graph_store),
        )

        assert kept[0].trigger_type is TriggerType.OPEN_LOOP_MATCH

    def test_it_is_dropped_when_nothing_is_open(self, graph_store):
        kept = ground_triggers(
            [RawTrigger(trigger_type="OPEN_LOOP_MATCH")],
            context=context_for(graph_store),
        )

        assert kept == ()

    def test_a_graph_that_cannot_answer_drops_the_reason(self):
        class Broken:
            def find_nodes(self, node_types, **kwargs):
                raise RuntimeError("the store is not answering")

        kept = ground_triggers(
            [RawTrigger(trigger_type="OPEN_LOOP_MATCH")],
            context=context_for(Broken()),
        )

        assert kept == ()


class TestReasonsThatNameNothing:
    @pytest.mark.parametrize(
        "kind",
        [
            "SOMATIC_MARKER",
            "IDENTITY_STATEMENT",
            "PROGRESS_CLAIM",
            "PATTERN_MENTION",
            "BELIEF_CHALLENGE",
        ],
    )
    def test_they_pass_through_without_a_lookup(self, graph_store, kind):
        kept = ground_triggers(
            [RawTrigger(trigger_type=kind)], context=context_for(graph_store)
        )

        assert kept[0].trigger_type.value == kind

    def test_a_real_area_of_life_is_kept(self, graph_store):
        kept = ground_triggers(
            [RawTrigger(trigger_type="PATTERN_MENTION", domain="SELF_CONCEPT")],
            context=context_for(graph_store),
        )

        assert kept[0].domain is Domain.SELF_CONCEPT

    def test_an_invented_area_is_cleared_but_the_reason_stays(self, graph_store):
        # The area only narrows a search. A wider search still works, so
        # throwing the whole reason away over it would lose more than it
        # saves.
        kept = ground_triggers(
            [RawTrigger(trigger_type="PATTERN_MENTION", domain="avoidance_resistance")],
            context=context_for(graph_store),
        )

        assert kept[0].domain is None
        assert kept[0].trigger_type is TriggerType.PATTERN_MENTION


class TestKeepingTheSetTidy:
    def test_reasons_come_back_with_the_exact_ones_first(
        self, graph_store, seed_person
    ):
        seed_person("Alex")

        kept = ground_triggers(
            [
                RawTrigger(trigger_type="SOMATIC_MARKER"),
                RawTrigger(trigger_type="PATTERN_MENTION"),
                RawTrigger(trigger_type="NAMED_PERSON", people=["Alex"]),
            ],
            context=context_for(graph_store),
        )

        assert [item.trigger_type for item in kept] == [
            TriggerType.NAMED_PERSON,
            TriggerType.PATTERN_MENTION,
            TriggerType.SOMATIC_MARKER,
        ]

    def test_two_reasons_of_one_kind_collapse_to_one(self, graph_store):
        kept = ground_triggers(
            [
                RawTrigger(trigger_type="PATTERN_MENTION", keywords=["first"]),
                RawTrigger(trigger_type="PATTERN_MENTION", keywords=["second"]),
            ],
            context=context_for(graph_store),
        )

        assert len(kept) == 1
        assert kept[0].keywords == ("first",)

    @pytest.mark.parametrize("named", ["NOT_A_REASON", "", "no_trigger"])
    def test_a_kind_that_does_not_exist_is_ignored(self, graph_store, named):
        kept = ground_triggers(
            [RawTrigger(trigger_type=named)], context=context_for(graph_store)
        )

        assert kept == ()

    def test_keywords_are_trimmed_deduplicated_and_capped(self, graph_store):
        kept = ground_triggers(
            [
                RawTrigger(
                    trigger_type="PATTERN_MENTION",
                    keywords=[" alone ", "alone", "", "fear", "resistance"],
                )
            ],
            context=context_for(graph_store, keyword_limit=2),
        )

        assert kept[0].keywords == ("alone", "fear")

    def test_every_kind_of_reason_has_a_check(self):
        # A kind with no entry would fall through to whichever branch
        # happened to be last, which is how an unchecked claim reaches the
        # search unnoticed.
        expected = {kind for kind in TriggerType if kind is not TriggerType.NO_TRIGGER}

        assert set(CHECKS) == expected


class TestReadingWordsBack:
    @pytest.mark.parametrize(
        "written,expected",
        [
            ("SELF_CONCEPT", Domain.SELF_CONCEPT),
            ("self concept", Domain.SELF_CONCEPT),
            ("Self-Concept", Domain.SELF_CONCEPT),
            ("relational", Domain.RELATIONAL),
        ],
    )
    def test_an_area_of_life_is_read_forgivingly(self, written, expected):
        assert parse_domain(written) is expected

    @pytest.mark.parametrize("written", [None, "", "avoidance_resistance"])
    def test_anything_else_is_no_area_at_all(self, written):
        assert parse_domain(written) is None

    def test_names_are_tidied_but_never_checked(self):
        # Kept even when no record matches, because a name the graph has
        # never seen is either somebody new or a sign the model is hearing
        # names nobody said.
        assert clean_names([" Alex ", "Alex", "", "Priya"]) == ("Alex", "Priya")
