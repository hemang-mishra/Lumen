"""
Tests for building the half of the write plan that reconciliation never sees.

Nothing here needs a database. Composing is pure: episodes and findings in,
a checked plan out. That is the point of keeping it separate from saving —
every structural mistake this could make is catchable without writing
anything anywhere.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from lumen.pipeline.orchestration import compose
from lumen.schemas.enums import (
    EntryClass,
    ObservationStatus,
    ReconciliationStatus,
    SourceModality,
)
from lumen.schemas.pipeline import GraphWritePlan

MOMENT = datetime(2026, 6, 11, 20, 0, tzinfo=UTC)


@pytest.fixture
def build(make_extraction_input, make_extraction, make_preprocessing):
    """Compose one episode's plan, with sensible defaults for everything."""

    def _build(
        extraction=None,
        outcome=None,
        *,
        payload=None,
        preprocessing=None,
        status: ReconciliationStatus = ReconciliationStatus.COMPLETE,
        previous_episode_id: str | None = None,
    ) -> GraphWritePlan:
        return compose.compose(
            payload or make_extraction_input(),
            extraction if extraction is not None else make_extraction("a finding"),
            outcome,
            preprocessing=preprocessing or make_preprocessing(),
            reconciliation_status=status,
            previous_episode_id=previous_episode_id,
            at=MOMENT,
        )

    return _build


def _types(plan: GraphWritePlan) -> list[str]:
    return [node.node_type for node in plan.nodes]


def _tables(plan: GraphWritePlan) -> list[str]:
    return [edge.table for edge in plan.edges]


class TestTheEpisodeRecord:
    def test_the_episode_is_created_first(self, build):
        plan = build()

        assert plan.nodes[0].node_type == "EpisodeNode"

    def test_it_carries_everything_the_episode_knows_about_itself(
        self, make_extraction_input, make_preprocessing
    ):
        payload = make_extraction_input(session_label="B")

        node = compose.build_episode_node(
            payload,
            preprocessing=make_preprocessing(),
            reconciliation_status=ReconciliationStatus.COMPLETE,
            at=MOMENT,
        )

        assert node.node_id == payload.episode.episode_id
        assert node.entry_id == payload.entry_id
        assert node.episode_summary == payload.episode.episode_summary
        assert node.raw_text_hash == payload.episode.raw_text_hash
        assert node.entry_class is EntryClass.REFLECTION
        assert node.source_modality is SourceModality.TEXT_ENTRY
        assert node.session_label == "B"

    def test_it_points_at_the_coreference_map_for_its_entry(
        self, make_extraction_input, make_preprocessing
    ):
        payload = make_extraction_input()

        node = compose.build_episode_node(
            payload,
            preprocessing=make_preprocessing(),
            reconciliation_status=ReconciliationStatus.COMPLETE,
            at=MOMENT,
        )

        assert node.coreference_map_id == compose.coreference_map_id(payload.entry_id)

    def test_the_same_entry_always_names_the_same_map(self):
        # Built by rule rather than at random, so re-processing an entry
        # points at the map it already has instead of orphaning it.
        assert compose.coreference_map_id("sess_a") == compose.coreference_map_id("sess_a")
        assert compose.coreference_map_id("sess_a") != compose.coreference_map_id("sess_b")

    def test_the_languages_of_the_original_are_recorded(
        self, make_extraction_input, make_preprocessing
    ):
        # What is stored may be a translation. Without this, somebody's own
        # words in their own language quietly become English.
        node = compose.build_episode_node(
            make_extraction_input(),
            preprocessing=make_preprocessing(languages=["hi", "en"]),
            reconciliation_status=ReconciliationStatus.COMPLETE,
            at=MOMENT,
        )

        assert node.language_tags == ["hi", "en"]

    def test_english_is_assumed_when_nothing_was_detected(
        self, make_extraction_input, make_preprocessing
    ):
        node = compose.build_episode_node(
            make_extraction_input(),
            preprocessing=make_preprocessing(languages=[]),
            reconciliation_status=ReconciliationStatus.COMPLETE,
            at=MOMENT,
        )

        assert node.language_tags == ["en"]

    def test_the_status_it_is_given_is_the_status_it_carries(
        self, make_extraction_input, make_preprocessing
    ):
        node = compose.build_episode_node(
            make_extraction_input(),
            preprocessing=make_preprocessing(),
            reconciliation_status=ReconciliationStatus.SUSPENDED,
            at=MOMENT,
        )

        assert node.reconciliation_status is ReconciliationStatus.SUSPENDED


class TestTheOrderOfRecords:
    def test_anchors_are_created_before_the_findings_they_explain(
        self, build, make_extraction
    ):
        plan = build(
            make_extraction("a finding", events=["a thing happened"], sessions=["thought about it"])
        )
        order = _types(plan)

        assert order.index("SessionNode") < order.index("ObservationNode")
        assert order.index("EventNode") < order.index("ObservationNode")

    def test_a_chain_is_created_before_its_own_steps(
        self, build, make_extraction, sample_causal_chain, sample_causal_step
    ):
        extraction = make_extraction("a finding").model_copy(
            update={
                "causal_chains": [sample_causal_chain],
                "causal_steps": [sample_causal_step],
            }
        )

        order = _types(build(extraction))

        assert order.index("CausalChainNode") < order.index("CausalStepNode")

    def test_findings_that_could_not_be_read_are_still_created(
        self, build, make_extraction, sample_observation
    ):
        # The reading failed; what the person wrote did not. Throwing it
        # away would lose the writing along with the failed analysis.
        failed = sample_observation.model_copy(
            update={
                "node_id": "obs_unreadable",
                "status": ObservationStatus.EXTRACTION_FAILED,
            }
        )
        extraction = make_extraction("a finding").model_copy(
            update={"failed_observations": [failed]}
        )

        plan = build(extraction)

        assert "obs_unreadable" in [node.node.node_id for node in plan.nodes]


class TestContainment:
    def test_every_finding_is_linked_to_its_episode(
        self, build, make_extraction
    ):
        plan = build(
            make_extraction("one", "two", events=["an event"], sessions=["a session"])
        )

        assert _tables(plan).count("contains_obs") == 2
        assert "contains_evt" in _tables(plan)
        assert "contains_sess" in _tables(plan)

    def test_a_chain_is_linked_to_its_episode_and_to_its_steps(
        self, build, make_extraction, sample_causal_chain, sample_causal_step
    ):
        extraction = make_extraction().model_copy(
            update={
                "causal_chains": [sample_causal_chain],
                "causal_steps": [sample_causal_step],
            }
        )

        tables = _tables(build(extraction))

        assert "contains_chain" in tables
        assert "chain_contains" in tables

    def test_a_step_belonging_to_no_chain_here_is_dropped(
        self, build, make_extraction, sample_causal_step, caplog
    ):
        # Keeping it would point the plan at a record nobody creates, and
        # the whole episode would be refused over one stray step.
        extraction = make_extraction().model_copy(
            update={"causal_steps": [sample_causal_step]}
        )

        plan = build(extraction)

        assert "chain_contains" not in _tables(plan)
        assert "causal step" in caplog.text

    def test_a_failed_finding_is_marked_as_such(
        self, build, make_extraction, sample_observation
    ):
        failed = sample_observation.model_copy(
            update={
                "node_id": "obs_unreadable",
                "status": ObservationStatus.EXTRACTION_FAILED,
            }
        )
        extraction = make_extraction().model_copy(
            update={"failed_observations": [failed]}
        )

        assert "failed_extraction" in _tables(build(extraction))

    def test_structural_links_carry_no_decision(self, build):
        # Nothing decided them. An episode contains what was found in it,
        # which is a fact about the writing rather than a judgement.
        plan = build()
        containment = next(e for e in plan.edges if e.table == "contains_obs")

        assert "decision_id" not in containment.properties()
        assert containment.properties()["valid_from"] is not None


class TestEpisodeOrdering:
    def test_an_episode_is_chained_to_the_one_before_it(self, build):
        plan = build(previous_episode_id="ep_2026_06_11_000")

        assert "follows_from" in _tables(plan)

    def test_the_first_episode_of_an_entry_chains_to_nothing(self, build):
        assert "follows_from" not in _tables(build(previous_episode_id=None))

    def test_the_previous_episode_is_allowed_to_already_exist(self, build):
        # It was saved by an earlier commit, so the plan must accept it as
        # an endpoint without trying to create it.
        plan = build(previous_episode_id="ep_2026_06_11_000")

        assert "ep_2026_06_11_000" in plan.existing_node_ids
        assert "ep_2026_06_11_000" not in [n.node.node_id for n in plan.nodes]


class TestMergingWithReconciliation:
    def test_the_decisions_records_are_added_after_the_episodes(
        self, build, make_extraction, reconciliation_outcome
    ):
        plan = build(make_extraction("a finding"), reconciliation_outcome)
        order = _types(plan)

        assert order[0] == "EpisodeNode"
        assert "PatternNode" in order
        assert order.index("ObservationNode") < order.index("PatternNode")

    def test_the_decisions_links_and_updates_come_across(
        self, build, make_extraction, reconciliation_outcome
    ):
        plan = build(make_extraction("a finding"), reconciliation_outcome)

        assert "branches_to_obs_pat" in _tables(plan)
        assert len(plan.bookkeeping) == len(reconciliation_outcome.write_plan.bookkeeping)

    def test_nothing_from_reconciliation_appears_when_it_never_ran(self, build):
        plan = build(outcome=None)

        assert plan.bookkeeping == []
        assert all(node.node_type != "PatternNode" for node in plan.nodes)

    def test_the_merged_plan_still_checks_itself(
        self, build, make_extraction, reconciliation_outcome
    ):
        # The point of merging before saving: the plan's own checks now
        # cover the whole episode instead of only the decisions half.
        plan = build(make_extraction("a finding"), reconciliation_outcome)
        created = {node.node.node_id for node in plan.nodes} | plan.existing_node_ids

        for edge in plan.edges:
            assert edge.from_node_id in created
            assert edge.to_node_id in created


class TestReadingTheOutcome:
    def test_a_settled_episode_is_complete(self, make_extraction, reconciliation_outcome):
        settled = reconciliation_outcome.model_copy(update={"escalations": []})

        assert (
            compose.status_for(make_extraction("a finding"), settled)
            is ReconciliationStatus.COMPLETE
        )

    def test_an_unreadable_episode_is_suspended(self, make_extraction):
        # An episode nobody could read looks exactly like an empty one.
        # Saying so is the only thing that keeps them apart.
        failed = make_extraction().model_copy(update={"read_failed": True})

        assert compose.status_for(failed, None) is ReconciliationStatus.SUSPENDED

    def test_an_episode_with_something_waiting_is_suspended(
        self, make_extraction, reconciliation_outcome
    ):
        assert reconciliation_outcome.escalations

        assert (
            compose.status_for(make_extraction("a finding"), reconciliation_outcome)
            is ReconciliationStatus.SUSPENDED
        )

    def test_an_episode_whose_decision_was_unreadable_is_suspended(
        self, make_extraction, reconciliation_outcome
    ):
        # Nothing was decided, but the entry is not empty. Recording it as
        # settled would file everything in it as needing no attention.
        unreadable = reconciliation_outcome.model_copy(
            update={"escalations": [], "decision_failed": True}
        )

        assert (
            compose.status_for(make_extraction("a finding"), unreadable)
            is ReconciliationStatus.SUSPENDED
        )

    def test_a_thin_episode_is_finished_rather_than_open(self, make_extraction):
        # It was never going to be reconciled, so nothing is outstanding.
        thin = make_extraction("a note", status=ObservationStatus.RAW_CAPTURE)

        assert compose.status_for(thin, None) is ReconciliationStatus.COMPLETE


class TestThinEntries:
    def test_an_entry_of_only_light_notes_is_thin(self, make_extraction):
        assert compose.is_thin(
            make_extraction("a note", status=ObservationStatus.RAW_CAPTURE)
        )

    def test_an_entry_with_a_real_finding_is_not(self, make_extraction):
        assert not compose.is_thin(make_extraction("a real finding"))

    def test_an_entry_with_an_event_is_not(self, make_extraction):
        thin_but_eventful = make_extraction(
            "a note", status=ObservationStatus.RAW_CAPTURE
        ).model_copy(update={"events": make_extraction(events=["went out"]).events})

        assert not compose.is_thin(thin_but_eventful)
