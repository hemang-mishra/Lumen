"""
Tests for the evidence that a long-running pattern is real.

Two rules carry this whole section, and both are about restraint. A chain
does not exist until enough separate occasions are behind it, because three
instances is a coincidence somebody can argue with. And the examples are
spread across the years rather than picked as the most striking, because
"most striking" cannot be computed the same way twice.

Counted in episodes rather than in findings throughout: one evening that
circles the same realisation four times is one occasion of it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from lumen.config import MaintenanceConfig
from lumen.pipeline.macroextraction import proof

NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)


@pytest.fixture
def evidence(graph_store, seed_pattern):
    """Put a pattern in the graph with a chosen number of occasions behind it."""

    def _build(
        pattern_id: str = "pat_comparison",
        *,
        occasions: int = 12,
        findings_each: int = 1,
        days_apart: int = 120,
        status: str = "ACTIVE",
    ) -> str:
        seed_pattern(pattern_id, name="Comparison destroys motivation", status=status)
        for index in range(occasions):
            when = (NOW - timedelta(days=days_apart * (occasions - index))).isoformat()
            episode_id = f"ep_{pattern_id}_{index}"
            for finding in range(findings_each):
                node_id = f"obs_{pattern_id}_{index}_{finding}"
                graph_store.write_node(
                    "ObservationNode",
                    {
                        "node_id": node_id,
                        "episode_id": episode_id,
                        "occurred_at": when,
                        "created_at": when,
                        "valid_from": when,
                        "type": "PATTERN",
                        "content": f"comparing again, time {index}",
                        "signal_strength": "STANDARD",
                        "provenance": "USER_GENERATED",
                        "verification_status": "IMPLICIT",
                        "extraction_confidence": "HIGH",
                        "status": "ACTIVE",
                    },
                )
                graph_store.write_edge("reinforces_obs_pat", node_id, pattern_id)
        return pattern_id

    return _build


class TestWhenAChainExists:
    def test_enough_separate_occasions_make_one(self, graph_store, evidence):
        evidence(occasions=12)

        chains = proof.find_proof_chains(graph_store, config=MaintenanceConfig())

        assert [chain.record_id for chain in chains] == ["pat_comparison"]
        assert chains[0].total_instances == 12

    def test_too_few_occasions_make_none(self, graph_store, evidence):
        # Three instances is a coincidence somebody can argue with.
        evidence(occasions=3)

        assert proof.find_proof_chains(graph_store, config=MaintenanceConfig()) == []

    def test_the_threshold_can_be_moved(self, graph_store, evidence):
        evidence(occasions=4)

        chains = proof.find_proof_chains(
            graph_store, config=MaintenanceConfig(proof_min_instances=4)
        )

        assert len(chains) == 1

    def test_one_talkative_evening_is_one_occasion(self, graph_store, evidence):
        # Counting findings would let a single night look like a month of
        # them.
        evidence(occasions=10, findings_each=4)

        chains = proof.find_proof_chains(graph_store, config=MaintenanceConfig())

        assert chains[0].total_instances == 10

    def test_a_superseded_pattern_gets_no_chain(self, graph_store, evidence):
        # Proving at length that somebody used to think something they have
        # since revised is the opposite of useful.
        evidence(occasions=12, status="SUPERSEDED")

        assert proof.find_proof_chains(graph_store, config=MaintenanceConfig()) == []


class TestWhatAChainSays:
    def test_it_reaches_from_the_first_occasion_to_the_last(self, graph_store, evidence):
        evidence(occasions=12, days_apart=120)

        chain = proof.find_proof_chains(graph_store, config=MaintenanceConfig())[0]

        assert chain.first_seen < chain.last_seen
        assert chain.span_days == pytest.approx((chain.last_seen - chain.first_seen).days)
        assert chain.span_years > 3

    def test_the_summary_is_counted_rather_than_written(self, graph_store, evidence):
        # The same sentence every time with two numbers changed. A model
        # given the job would word it differently in every report while
        # adding nothing, and could reach for detail nothing established.
        evidence(occasions=14, days_apart=130)

        chain = proof.find_proof_chains(graph_store, config=MaintenanceConfig())[0]

        assert "14 separate occasions" in chain.summary
        assert str(chain.span_years) in chain.summary

    def test_every_example_names_a_real_episode(self, graph_store, evidence):
        # So a person can follow any line of it back to what they wrote.
        evidence(occasions=12)

        chain = proof.find_proof_chains(graph_store, config=MaintenanceConfig())[0]

        assert all(instance.episode_id.startswith("ep_") for instance in chain.key_instances)
        assert all(instance.excerpt for instance in chain.key_instances)


class TestChoosingTheExamples:
    def test_five_are_shown_out_of_many(self, graph_store, evidence):
        evidence(occasions=20)

        chain = proof.find_proof_chains(graph_store, config=MaintenanceConfig())[0]

        assert len(chain.key_instances) == 5

    def test_they_are_the_oldest_and_the_newest_and_between(self, graph_store, evidence):
        # Spread is arithmetic, and it serves the point better anyway: what
        # makes a chain convincing is the same thing happening in
        # circumstances that had nothing else in common.
        evidence(occasions=20)

        chain = proof.find_proof_chains(graph_store, config=MaintenanceConfig())[0]
        shown = [instance.happened_at for instance in chain.key_instances]

        assert shown == sorted(shown)
        assert shown[0] == chain.first_seen
        assert shown[-1] == chain.last_seen

    def test_asking_for_one_gives_the_oldest(self, graph_store, evidence):
        evidence(occasions=12)

        chain = proof.find_proof_chains(
            graph_store, config=MaintenanceConfig(proof_key_instances=1)
        )[0]

        assert [i.happened_at for i in chain.key_instances] == [chain.first_seen]

    def test_asking_for_more_than_there_are_gives_all_of_them(self, graph_store, evidence):
        evidence(occasions=10)

        chain = proof.find_proof_chains(
            graph_store, config=MaintenanceConfig(proof_key_instances=50)
        )[0]

        assert len(chain.key_instances) == 10

    def test_the_same_history_always_picks_the_same_examples(self, graph_store, evidence):
        evidence(occasions=20)

        first = proof.find_proof_chains(graph_store, config=MaintenanceConfig())[0]
        second = proof.find_proof_chains(graph_store, config=MaintenanceConfig())[0]

        assert [i.episode_id for i in first.key_instances] == [
            i.episode_id for i in second.key_instances
        ]


class TestLessons:
    def test_a_lesson_is_proved_by_the_episodes_it_names(self, graph_store):
        # Lessons are the one standing record with no links back to what
        # taught them. The episodes are written on the lesson itself.
        episodes = []
        for index in range(12):
            when = (NOW - timedelta(days=100 * (12 - index))).isoformat()
            episode_id = f"ep_lesson_{index}"
            graph_store.write_node(
                "EpisodeNode",
                {
                    "node_id": episode_id,
                    "entry_id": f"sess_{index}",
                    "occurred_at": when,
                    "created_at": when,
                    "valid_from": when,
                    "event_date": when[:10],
                    "session_label": "evening",
                    "source_modality": "TEXT_ENTRY",
                    "entry_class": "REFLECTIVE",
                    "episode_summary": f"the evening it happened again, {index}",
                    "episode_index": 1,
                    "total_episodes_in_entry": 1,
                    "reconciliation_status": "COMPLETE",
                    "raw_text_hash": f"h{index}",
                },
            )
            episodes.append(episode_id)

        graph_store.write_node(
            "LessonNode",
            {
                "node_id": "les_slow_down",
                "created_at": NOW.isoformat(),
                "valid_from": NOW.isoformat(),
                "lesson_statement": "Slowing down settles the panic",
                "domain": "EMOTIONAL",
                "signal_strength": "HIGH",
                "lesson_confidence": 0.8,
                "status": "ACTIVE",
                "evidence_episodes": episodes,
            },
        )

        chains = proof.find_proof_chains(graph_store, config=MaintenanceConfig())

        assert [chain.record_id for chain in chains] == ["les_slow_down"]
        assert chains[0].total_instances == 12


class TestWhenALessonNamesSomethingOdd:
    def test_a_lesson_naming_no_episodes_gets_no_chain(self, graph_store):
        graph_store.write_node(
            "LessonNode",
            {
                "node_id": "les_empty",
                "created_at": NOW.isoformat(),
                "valid_from": NOW.isoformat(),
                "lesson_statement": "Something learned from nothing recorded",
                "domain": "EMOTIONAL",
                "signal_strength": "STANDARD",
                "lesson_confidence": 0.5,
                "status": "ACTIVE",
                "evidence_episodes": [],
            },
        )

        assert proof.find_proof_chains(graph_store, config=MaintenanceConfig()) == []

    def test_an_episode_with_no_readable_date_is_skipped(self, graph_store):
        # A list of identifiers cannot say how far a chain reaches, so an
        # occasion with no date is not an occasion this can show.
        graph_store.write_node(
            "LessonNode",
            {
                "node_id": "les_undated",
                "created_at": NOW.isoformat(),
                "valid_from": NOW.isoformat(),
                "lesson_statement": "Learned from something undated",
                "domain": "EMOTIONAL",
                "signal_strength": "STANDARD",
                "lesson_confidence": 0.5,
                "status": "ACTIVE",
                "evidence_episodes": ["ep_missing"] * 12,
            },
        )

        assert proof.find_proof_chains(graph_store, config=MaintenanceConfig()) == []


class TestOrderAndLimits:
    def test_the_strongest_evidence_comes_first(self, graph_store, evidence):
        evidence("pat_a", occasions=12)
        evidence("pat_b", occasions=18)

        chains = proof.find_proof_chains(graph_store, config=MaintenanceConfig())

        assert [chain.record_id for chain in chains] == ["pat_b", "pat_a"]

    def test_a_finding_with_no_episode_is_skipped(self, graph_store, seed_pattern):
        seed_pattern("pat_orphan")

        assert proof.find_proof_chains(graph_store, config=MaintenanceConfig()) == []

    def test_an_empty_graph_finds_nothing(self, graph_store):
        assert proof.find_proof_chains(graph_store, config=MaintenanceConfig()) == []
