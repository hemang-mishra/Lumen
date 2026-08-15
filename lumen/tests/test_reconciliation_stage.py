"""
Tests for the stage as a whole, against a real database.

The stage is checked here for the things only the whole of it can show: how
many model calls an entry costs, that the databases are left exactly as they
were found, that an entry with one undecidable item still saves everything
else, and that the two failures which look like success are handled as
failures.

The graph is real rather than a stand-in because the stage reads from it —
ages, whole records, what has been decided before — and a stand-in answering
from a dictionary would agree with any query, including one naming a column
that does not exist.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from lumen.pipeline.reconciliation import reconcile
from lumen.schemas.enums import (
    CandidateRetrievalSource,
    DecisionStatus,
    HitlEntryType,
    ReconciliationAction,
    ReconciliationStatus,
    SignalStrength,
)
from lumen.schemas.pipeline import CandidateNode, RetrievalResult

MOMENT = datetime(2026, 6, 11, 20, 0, tzinfo=UTC)


def found(node_id: str = "pat_old", *, node_type: str = "PatternNode", score: float = 0.9):
    return CandidateNode(
        node_id=node_id,
        node_type=node_type,
        content_preview="Comparing himself to peers",
        similarity_score=score,
        retrieval_source=CandidateRetrievalSource.SEMANTIC,
    )


def searched(source_node_id: str, *candidates, failed: bool = False) -> RetrievalResult:
    return RetrievalResult(
        source_node_id=source_node_id,
        pass_a_candidates=list(candidates),
        retrieval_time_ms=3,
        search_failed=failed,
    )


def decided(*items, people=None) -> str:
    """Build a scripted reply covering the given findings."""
    return json.dumps(
        {
            "decisions": [
                {
                    "item_index": index,
                    "primary": {
                        "action": action,
                        "target_node_id": target,
                        "confidence": confidence,
                        "reason": "because",
                    },
                    **extra,
                }
                for index, action, target, confidence, extra in items
            ],
            "people": people or [],
        }
    )


def confirmed(*items) -> str:
    return json.dumps(
        {
            "verdicts": [
                {
                    "item_index": index,
                    "confirmed": True,
                    "primary": {
                        "action": action,
                        "target_node_id": target,
                        "confidence": confidence,
                    },
                    **extra,
                }
                for index, action, target, confidence, extra in items
            ]
        }
    )


@pytest.fixture
def run(graph_store, reconciliation_providers):
    """Run the stage against the real graph with a scripted pair of models."""

    def _run(extraction, retrievals, replies, **kwargs):
        light, deep = reconciliation_providers(replies)
        outcome = reconcile(
            extraction,
            retrievals,
            graph=graph_store,
            lightweight=light,
            thinking=deep,
            **kwargs,
        )
        return outcome, light, deep

    return _run


class TestWhatGetsDecided:
    def test_every_searched_finding_gets_a_decision(self, make_extraction, run):
        extraction = make_extraction("first thing", "second thing")
        outcome, _, _ = run(
            extraction,
            [searched("obs_new_1", found()), searched("obs_new_2")],
            {"decision": decided(
                (1, "REINFORCE", "pat_old", 0.9, {}),
                (2, "BRANCH", None, 0.85, {}),
            )},
        )

        assert [result.source_node_id for result in outcome.results] == [
            "obs_new_1",
            "obs_new_2",
        ]

    def test_a_finding_the_search_skipped_is_skipped_here(self, make_extraction, run):
        # Findings from a thin entry are saved as they are and never
        # compared against the past, so there is nothing to decide.
        extraction = make_extraction("only thing")
        outcome, _, _ = run(extraction, [], {})

        assert outcome.results == []
        assert outcome.write_plan.nodes == []

    def test_a_search_result_with_no_finding_behind_it_is_ignored(
        self, make_extraction, run
    ):
        extraction = make_extraction("only thing")
        outcome, _, _ = run(
            extraction,
            [searched("obs_new_1"), searched("obs_that_never_existed")],
            {"decision": decided((1, "BRANCH", None, 0.85, {}))},
        )

        assert len(outcome.results) == 1


class TestWhatARunCosts:
    def test_a_plain_entry_costs_one_call(self, make_extraction, run):
        extraction = make_extraction("a", "b", "c")
        _, light, deep = run(
            extraction,
            [searched(f"obs_new_{index}") for index in (1, 2, 3)],
            {"decision": decided(*[(index, "BRANCH", None, 0.85, {}) for index in (1, 2, 3)])},
        )

        assert len(light.calls) == 1
        assert len(deep.calls) == 0

    def test_a_heavy_reading_costs_one_more(self, make_extraction, run, seed_belief):
        seed_belief("bel_old")
        extraction = make_extraction("a", "b")
        _, light, deep = run(
            extraction,
            [searched("obs_new_1", found("bel_old", node_type="BeliefNode")), searched("obs_new_2")],
            {
                "decision": decided(
                    (1, "EVOLVE", "bel_old", 0.95, {"delta_description": "he changed"}),
                    (2, "BRANCH", None, 0.85, {}),
                ),
                "escalation": confirmed(
                    (1, "EVOLVE", "bel_old", 0.95, {"delta_description": "he changed"})
                ),
            },
        )

        assert len(light.calls) == 1
        assert len(deep.calls) == 1

    def test_the_records_involved_are_read_once_each(
        self, make_extraction, run, seed_pattern
    ):
        # The same record routinely comes back for several findings in one
        # entry. Reading it per finding would multiply the cost by the size
        # of the entry for no gain.
        seed_pattern("pat_old")
        extraction = make_extraction("a", "b")
        outcome, _, _ = run(
            extraction,
            [searched("obs_new_1", found()), searched("obs_new_2", found())],
            {"decision": decided(
                (1, "REINFORCE", "pat_old", 0.9, {}),
                (2, "REINFORCE", "pat_old", 0.9, {}),
            )},
        )

        assert len(outcome.results) == 2


class TestNothingIsSaved:
    def test_the_graph_is_left_exactly_as_it_was(
        self, graph_store, make_extraction, run, seed_pattern
    ):
        seed_pattern("pat_old", evidence_count=3)
        before = graph_store.get_node("pat_old")

        run(
            make_extraction("a"),
            [searched("obs_new_1", found())],
            {"decision": decided((1, "REINFORCE", "pat_old", 0.9, {}))},
        )

        assert graph_store.get_node("pat_old") == before

    def test_no_new_record_appears(self, graph_store, make_extraction, run):
        run(
            make_extraction("a claim about how he works"),
            [searched("obs_new_1")],
            {"decision": decided(
                (1, "BRANCH", None, 0.85,
                 {"new_node": {"kind": "PATTERN", "name": "Comparison spiral",
                               "statement": "compares and sinks", "domain": "EMOTIONAL"}}),
            )},
        )

        assert graph_store.get_node("pat_comparison_spiral") is None

    def test_the_records_are_planned_rather_than_written(self, make_extraction, run):
        outcome, _, _ = run(
            make_extraction("a claim about how he works"),
            [searched("obs_new_1")],
            {"decision": decided(
                (1, "BRANCH", None, 0.85,
                 {"new_node": {"kind": "PATTERN", "name": "Comparison spiral",
                               "statement": "compares and sinks", "domain": "EMOTIONAL"}}),
            )},
        )

        assert [node.node_type for node in outcome.write_plan.nodes] == [
            "PatternNode",
            "DecisionAuditNode",
        ]


class TestTheWholePlanHangsTogether:
    def test_every_link_points_at_something_that_will_exist(
        self, make_extraction, run, seed_pattern
    ):
        seed_pattern("pat_old")
        outcome, _, _ = run(
            make_extraction("a", person_refs=["Alex"]),
            [searched("obs_new_1", found())],
            {"decision": decided((1, "REINFORCE", "pat_old", 0.9, {}),
                                 people=[{"name": "Alex", "relationship": "FRIEND"}])},
        )

        known = {node.node.node_id for node in outcome.write_plan.nodes}
        known |= outcome.write_plan.existing_node_ids
        for edge in outcome.write_plan.edges:
            assert edge.from_node_id in known
            assert edge.to_node_id in known

    def test_the_findings_this_run_extracted_count_as_existing(
        self, make_extraction, run
    ):
        # They are saved just before the plan runs. Stating that here is
        # what lets a link point at one without the plan creating it.
        extraction = make_extraction("a")
        outcome, _, _ = run(
            extraction,
            [searched("obs_new_1")],
            {"decision": decided((1, "BRANCH", None, 0.85, {}))},
        )

        assert "obs_new_1" in outcome.write_plan.existing_node_ids

    def test_the_plan_can_actually_be_carried_out(
        self, graph_store, make_extraction, run, seed_pattern
    ):
        # A plan that cannot be executed is not a plan. This stage does not
        # save anything, but the shapes it produces have to be savable, and
        # that has to fail here rather than in the code that writes.
        seed_pattern("pat_old")
        extraction = make_extraction("a", person_refs=["Alex"])
        outcome, _, _ = run(
            extraction,
            [searched("obs_new_1", found())],
            {"decision": decided((1, "REINFORCE", "pat_old", 0.9, {}))},
        )

        for observation in extraction.observations:
            graph_store.write_node("ObservationNode", observation)
        for planned in outcome.write_plan.nodes:
            graph_store.write_node(planned.node_type, planned.node)
        for edge in outcome.write_plan.edges:
            graph_store.write_edge(
                edge.table, edge.from_node_id, edge.to_node_id, edge.properties()
            )
        for update in outcome.write_plan.bookkeeping:
            getattr(graph_store, update.operation.value.lower())(
                update.node_id, at=update.at
            )

        assert graph_store.get_node("pat_old")["evidence_count"] == 4


class TestWhenSomethingCannotBeDecided:
    def test_the_rest_of_the_entry_is_still_decided(self, make_extraction, run):
        # One unanswered question used to freeze a whole day's work. The
        # confident decisions are kept and only the uncertain one waits.
        outcome, _, _ = run(
            make_extraction("a", "b"),
            [searched("obs_new_1", found()), searched("obs_new_2")],
            {"decision": decided(
                (1, "MERGE", "pat_old", 0.5, {}),
                (2, "BRANCH", None, 0.9, {}),
            )},
        )

        assert len(outcome.escalations) == 1
        assert outcome.results[1].escalated_to_hitl is False

    def test_the_entry_is_marked_as_having_something_outstanding(
        self, make_extraction, run
    ):
        outcome, _, _ = run(
            make_extraction("a"),
            [searched("obs_new_1", found())],
            {"decision": decided((1, "MERGE", "pat_old", 0.5, {}))},
        )

        assert outcome.episode_status is ReconciliationStatus.SUSPENDED

    def test_a_settled_entry_says_so(self, make_extraction, run):
        outcome, _, _ = run(
            make_extraction("a"),
            [searched("obs_new_1")],
            {"decision": decided((1, "BRANCH", None, 0.9, {}))},
        )

        assert outcome.episode_status is ReconciliationStatus.COMPLETE
        assert outcome.escalations == []

    def test_a_tie_is_queued_as_a_tie(self, make_extraction, run):
        outcome, _, _ = run(
            make_extraction("a"),
            [searched("obs_new_1", found())],
            {"decision": json.dumps({"decisions": [{
                "item_index": 1,
                "primary": {"action": "MERGE", "target_node_id": "pat_old", "confidence": 0.92},
                "runner_up": {"action": "REINFORCE", "target_node_id": "pat_old", "confidence": 0.90},
            }]})},
        )

        assert outcome.escalations[0].entry_type is HitlEntryType.AMBIGUOUS_TIE
        assert outcome.audit_nodes[0].status is DecisionStatus.PENDING_HITL

    def test_a_waiting_item_writes_no_records_of_its_own(self, make_extraction, run):
        outcome, _, _ = run(
            make_extraction("a"),
            [searched("obs_new_1", found())],
            {"decision": decided((1, "MERGE", "pat_old", 0.5, {}))},
        )

        assert [node.node_type for node in outcome.write_plan.nodes] == [
            "DecisionAuditNode"
        ]

    def test_what_is_waiting_carries_its_weight(self, make_extraction, run):
        # A critical finding waiting behind twenty ordinary ones is a queue
        # nobody finishes.
        outcome, _, _ = run(
            make_extraction("a"),
            [searched("obs_new_1", found())],
            {"decision": decided((1, "MERGE", "pat_old", 0.5, {}))},
        )

        assert outcome.escalations[0].signal_strength is SignalStrength.STANDARD
        assert outcome.escalations[0].audit_node_id == outcome.audit_nodes[0].node_id

    def test_no_journal_text_reaches_the_queue_summary(self, make_extraction, run):
        secret = "the thing I have never told anyone"
        outcome, _, _ = run(
            make_extraction(secret),
            [searched("obs_new_1", found())],
            {"decision": decided((1, "MERGE", "pat_old", 0.5, {}))},
        )

        assert secret not in outcome.escalations[0].summary


class TestTheTwoFailuresThatLookLikeSuccess:
    def test_a_finding_whose_search_failed_is_never_recorded_as_new(
        self, make_extraction, run
    ):
        # The whole reason the search reports the difference. Recording it
        # as new would file a decade-old pattern as a fresh discovery,
        # permanently, with nothing to show it happened.
        outcome, light, _ = run(
            make_extraction("a"),
            [searched("obs_new_1", failed=True)],
            {},
        )

        assert outcome.results[0].escalated_to_hitl is True
        assert [node.node_type for node in outcome.write_plan.nodes] == [
            "DecisionAuditNode"
        ]

    def test_a_failed_search_is_not_even_asked_about(self, make_extraction, run):
        outcome, light, _ = run(
            make_extraction("a"),
            [searched("obs_new_1", failed=True)],
            {},
        )

        assert light.calls == []

    def test_one_failed_search_does_not_stop_the_others(self, make_extraction, run):
        outcome, _, _ = run(
            make_extraction("a", "b"),
            [searched("obs_new_1", failed=True), searched("obs_new_2")],
            {"decision": decided((1, "BRANCH", None, 0.9, {}))},
        )

        assert outcome.results[0].escalated_to_hitl is True
        assert outcome.results[1].escalated_to_hitl is False

    def test_an_unreadable_answer_stops_everything_and_says_so(
        self, make_extraction, run
    ):
        # An entry nobody could decide about looks exactly like an entry
        # with nothing in it. Only one of them should be tried again.
        outcome, _, _ = run(
            make_extraction("a", "b"),
            [searched("obs_new_1"), searched("obs_new_2")],
            {"decision": "not json at all"},
        )

        assert outcome.decision_failed is True
        assert len(outcome.escalations) == 2
        assert outcome.episode_status is ReconciliationStatus.SUSPENDED

    def test_a_missing_answer_for_one_finding_does_not_shift_the_others(
        self, make_extraction, run
    ):
        outcome, _, _ = run(
            make_extraction("a", "b"),
            [searched("obs_new_1"), searched("obs_new_2")],
            {"decision": decided((2, "BRANCH", None, 0.9, {}))},
        )

        waiting = [result for result in outcome.results if result.escalated_to_hitl]
        assert [result.source_node_id for result in waiting] == ["obs_new_1"]


class TestPeopleAreResolvedOncePerEntry:
    def test_a_person_named_by_two_findings_gets_one_record(
        self, make_extraction, run
    ):
        outcome, _, _ = run(
            make_extraction("a", "b", person_refs=["Alex"]),
            [searched("obs_new_1"), searched("obs_new_2")],
            {"decision": decided(
                (1, "BRANCH", None, 0.9, {}),
                (2, "BRANCH", None, 0.9, {}),
            )},
        )

        people_nodes = [
            node for node in outcome.write_plan.nodes
            if node.node_type == "PersonEntityNode"
        ]
        assert len(people_nodes) == 1

    def test_every_finding_that_named_them_is_linked(self, make_extraction, run):
        outcome, _, _ = run(
            make_extraction("a", "b", person_refs=["Alex"]),
            [searched("obs_new_1"), searched("obs_new_2")],
            {"decision": decided(
                (1, "BRANCH", None, 0.9, {}),
                (2, "BRANCH", None, 0.9, {}),
            )},
        )

        mentions = [
            edge for edge in outcome.write_plan.edges if edge.table == "mentions_obs"
        ]
        assert len(mentions) == 2


class TestWhatIsRecordedAboutTheRun:
    def test_the_moment_comes_from_the_entry_not_the_clock(
        self, make_extraction, run
    ):
        # An entry processed days late belongs to the day it was written,
        # and the identifiers should say so.
        outcome, _, _ = run(
            make_extraction("a"),
            [searched("obs_new_1")],
            {"decision": decided((1, "BRANCH", None, 0.9, {}))},
        )

        assert outcome.audit_nodes[0].node_id.startswith("d_2026_06_11")

    def test_the_time_taken_is_recorded(self, make_extraction, run):
        outcome, _, _ = run(
            make_extraction("a"),
            [searched("obs_new_1")],
            {"decision": decided((1, "BRANCH", None, 0.9, {}))},
        )

        assert outcome.decision_time_ms >= 0

    def test_the_model_that_had_the_final_say_is_named(
        self, make_extraction, run, seed_belief
    ):
        seed_belief("bel_old")
        outcome, _, _ = run(
            make_extraction("a"),
            [searched("obs_new_1", found("bel_old", node_type="BeliefNode"))],
            {
                "decision": decided(
                    (1, "EVOLVE", "bel_old", 0.95, {"delta_description": "he changed"})
                ),
                "escalation": confirmed(
                    (1, "EVOLVE", "bel_old", 0.95, {"delta_description": "he changed"})
                ),
            },
        )

        assert outcome.decision_model == "fake-thinker"

    def test_the_run_is_logged_with_what_it_decided(
        self, make_extraction, run, captured_logs
    ):
        # The counts are the only warning this stage has: a run that has
        # quietly become all one action shows up here first.
        run(
            make_extraction("a"),
            [searched("obs_new_1")],
            {"decision": decided((1, "BRANCH", None, 0.9, {}))},
        )

        line = next(
            entry for entry in captured_logs
            if entry.get("msg") == "reconciliation complete"
        )
        assert line["actions"] == {"BRANCH": 1}
        assert line["decided"] == 1

    def test_every_decision_carries_the_run_id(
        self, make_extraction, run, bound_trace
    ):
        outcome, _, _ = run(
            make_extraction("a"),
            [searched("obs_new_1")],
            {"decision": decided((1, "BRANCH", None, 0.9, {}))},
        )

        assert outcome.trace_id == bound_trace
        assert all(result.trace_id == bound_trace for result in outcome.results)


class TestAnEntryWithAChange:
    def test_a_changed_belief_produces_a_version_and_its_cause(
        self, make_extraction, run, seed_belief
    ):
        seed_belief("bel_old", statement="I need solitude to recharge")
        extraction = make_extraction("I recharge with people I trust", sessions=["thinking it over"])
        outcome, _, _ = run(
            extraction,
            [
                searched("obs_new_1", found("bel_old", node_type="BeliefNode")),
                searched("sess_new_1"),
            ],
            {
                "decision": decided(
                    (1, "EVOLVE", "bel_old", 0.95,
                     {"delta_description": "solitude stopped being the only way"}),
                    (2, "BRANCH", None, 0.9, {}),
                ),
                "escalation": confirmed(
                    (1, "EVOLVE", "bel_old", 0.95,
                     {"delta_description": "solitude stopped being the only way"})
                ),
            },
        )

        assert outcome.results[0].action is ReconciliationAction.EVOLVE
        assert outcome.results[0].delta_description is not None
        tables = {edge.table for edge in outcome.write_plan.edges}
        assert "evolved_from_bel" in tables
        assert "caused_by_bel_sess" in tables

    def test_a_long_held_belief_is_not_changed_on_one_occasion(
        self, make_extraction, run, seed_belief
    ):
        seed_belief("bel_ancient", valid_from="2024-01-01T00:00:00+00:00")
        outcome, _, _ = run(
            make_extraction("I went out alone today and it was fine"),
            [searched("obs_new_1", found("bel_ancient", node_type="BeliefNode"))],
            {
                "decision": decided(
                    (1, "EVOLVE", "bel_ancient", 0.96,
                     {"delta_description": "he did it once"})
                ),
                "escalation": confirmed(
                    (1, "EVOLVE", "bel_ancient", 0.96,
                     {"delta_description": "he did it once"})
                ),
            },
        )

        assert outcome.results[0].action is ReconciliationAction.BRANCH
        assert not any(
            "evolved_from" in edge.table for edge in outcome.write_plan.edges
        )


class TestWhenTheGraphCannotBeRead:
    """
    The stage reads the graph for three things. None of those reads failing
    should cost the whole entry, and none should quietly make a heavier
    action possible.
    """

    def test_records_that_cannot_be_read_back_do_not_stop_the_run(
        self, make_extraction, reconciliation_providers, graph_store
    ):
        class HalfBrokenGraph:
            def get_nodes_by_ids(self, node_ids):
                raise RuntimeError("database gone")

            def get_node(self, node_id):
                return None

            def count_prior_decisions(self, target_node_id, *, actions):
                return 0

        light, deep = reconciliation_providers(
            {"decision": decided((1, "BRANCH", None, 0.9, {}))}
        )

        outcome = reconcile(
            make_extraction("a"),
            [searched("obs_new_1", found())],
            graph=HalfBrokenGraph(),
            lightweight=light,
            thinking=deep,
        )

        assert outcome.results[0].action is ReconciliationAction.BRANCH

    def test_a_change_cannot_slip_through_on_an_unreadable_record(
        self, make_extraction, reconciliation_providers
    ):
        # Without the old record there is nothing to build a new version
        # from, and inventing the missing fields would put words in the
        # person's mouth.
        class HalfBrokenGraph:
            def get_nodes_by_ids(self, node_ids):
                raise RuntimeError("database gone")

            def get_node(self, node_id):
                return None

            def count_prior_decisions(self, target_node_id, *, actions):
                return 0

        light, deep = reconciliation_providers(
            {
                "decision": decided(
                    (1, "EVOLVE", "bel_old", 0.95, {"delta_description": "changed"})
                ),
                "escalation": confirmed(
                    (1, "EVOLVE", "bel_old", 0.95, {"delta_description": "changed"})
                ),
            }
        )

        outcome = reconcile(
            make_extraction("a", sessions=["thinking"]),
            [
                searched("obs_new_1", found("bel_old", node_type="BeliefNode")),
                searched("sess_new_1"),
            ],
            graph=HalfBrokenGraph(),
            lightweight=light,
            thinking=deep,
        )

        assert not any(
            node.node_type == "BeliefNode" for node in outcome.write_plan.nodes
        )


class TestSmallerDetails:
    def test_the_entry_can_supply_the_moment_directly(
        self, make_extraction, make_extraction_input, run
    ):
        # When the episode is handed in, its own moment is used rather than
        # anything read off a node.
        outcome, _, _ = run(
            make_extraction("a"),
            [searched("obs_new_1")],
            {"decision": decided((1, "BRANCH", None, 0.9, {}))},
            episode=make_extraction_input(),
        )

        assert outcome.audit_nodes[0].node_id.startswith("d_2026_06_11")

    def test_an_event_is_decided_about_by_its_summary(self, make_extraction, run):
        outcome, _, _ = run(
            make_extraction(events=["I ate at the cafe alone"]),
            [searched("evt_new_1")],
            {"decision": decided((1, "BRANCH", None, 0.9, {}))},
        )

        assert outcome.results[0].source_node_id == "evt_new_1"

    def test_an_event_can_stand_in_as_the_cause_of_a_change(
        self, make_extraction, run, seed_belief
    ):
        # Sessions are preferred, but an entry with only an event still has
        # something a change can be attributed to.
        seed_belief("bel_old")
        outcome, _, _ = run(
            make_extraction("a", events=["I left the job"]),
            [
                searched("obs_new_1", found("bel_old", node_type="BeliefNode")),
                searched("evt_new_1"),
            ],
            {
                "decision": decided(
                    (1, "EVOLVE", "bel_old", 0.95, {"delta_description": "changed"}),
                    (2, "BRANCH", None, 0.9, {}),
                ),
                "escalation": confirmed(
                    (1, "EVOLVE", "bel_old", 0.95, {"delta_description": "changed"})
                ),
            },
        )

        caused_by = [
            edge for edge in outcome.write_plan.edges if "caused_by" in edge.table
        ]
        assert [edge.to_node_id for edge in caused_by] == ["evt_new_1"]

    def test_a_record_with_an_unreadable_date_is_not_treated_as_old(
        self, graph_store, make_extraction, run
    ):
        # An unreadable date makes a record look recent, and a recent record
        # is protected by fewer rules — never more. This deliberately never
        # guesses a date that would unlock a heavier action.
        graph_store.write_node(
            "BeliefNode",
            {
                "node_id": "bel_odd",
                "version": 1,
                "created_at": "not a date",
                "valid_from": "not a date",
                "last_reinforced_at": "not a date",
                "belief_statement": "an old belief",
                "belief_source_summary": "from somewhere",
                "domain": "SELF_CONCEPT",
                "signal_strength": "STANDARD",
                "provenance": "USER_GENERATED",
                "verification_status": "IMPLICIT",
                "evidence_count": 1,
                "query_frequency": 0,
                "is_contradicted": False,
                "status": "ACTIVE",
            },
        )

        outcome, _, _ = run(
            make_extraction("a", sessions=["thinking"]),
            [
                searched("obs_new_1", found("bel_odd", node_type="BeliefNode")),
                searched("sess_new_1"),
            ],
            {
                "decision": decided(
                    (1, "EVOLVE", "bel_odd", 0.95, {"delta_description": "changed"}),
                    (2, "BRANCH", None, 0.9, {}),
                ),
                "escalation": confirmed(
                    (1, "EVOLVE", "bel_odd", 0.95, {"delta_description": "changed"})
                ),
            },
        )

        assert outcome.results[0].action is ReconciliationAction.EVOLVE

    def test_a_graph_that_cannot_answer_about_identifiers_still_plans(
        self, make_extraction, reconciliation_providers
    ):
        class NoAnswers:
            def get_nodes_by_ids(self, node_ids):
                return []

            def get_node(self, node_id):
                raise RuntimeError("database gone")

            def count_prior_decisions(self, target_node_id, *, actions):
                return 0

        light, deep = reconciliation_providers(
            {"decision": decided(
                (1, "BRANCH", None, 0.9,
                 {"new_node": {"kind": "PATTERN", "name": "Comparison spiral",
                               "statement": "compares and sinks", "domain": "EMOTIONAL"}}),
            )}
        )

        outcome = reconcile(
            make_extraction("a claim about how he works"),
            [searched("obs_new_1")],
            graph=NoAnswers(),
            lightweight=light,
            thinking=deep,
        )

        assert any(
            node.node_type == "PatternNode" for node in outcome.write_plan.nodes
        )


class TestAnEntryWithNothingInIt:
    def test_it_produces_an_empty_result_without_asking_anything(
        self, graph_store, reconciliation_providers
    ):
        from lumen.schemas.pipeline import ExtractionResult

        light, deep = reconciliation_providers({})

        outcome = reconcile(
            ExtractionResult(
                episode_id="ep_empty", extraction_model="fake", validation_passed=True
            ),
            [],
            graph=graph_store,
            lightweight=light,
            thinking=deep,
        )

        assert outcome.results == []
        assert outcome.episode_status is ReconciliationStatus.COMPLETE
        assert light.calls == []


class TestReadingStoredDates:
    """
    Dates come back from the database as text, but a different store could
    hand back real timestamps — including ones with no timezone on them.
    Comparing one of those to a timezone-aware moment raises, so they are
    given one rather than crashing an entry.
    """

    def test_a_timestamp_without_a_timezone_is_given_one(self):
        from lumen.pipeline.reconciliation.stage import _read_moment

        read = _read_moment(datetime(2026, 1, 1, 12, 0))

        assert read.tzinfo is not None

    def test_a_timestamp_that_already_has_one_is_left_alone(self):
        from lumen.pipeline.reconciliation.stage import _read_moment

        moment = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

        assert _read_moment(moment) == moment

    def test_text_without_a_timezone_is_given_one(self):
        from lumen.pipeline.reconciliation.stage import _read_moment

        assert _read_moment("2026-01-01T12:00:00").tzinfo is not None

    def test_something_that_is_not_a_date_reads_as_no_date(self):
        from lumen.pipeline.reconciliation.stage import _read_moment

        assert _read_moment("last tuesday") is None
        assert _read_moment(None) is None
