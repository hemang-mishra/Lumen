"""
Tests for turning decisions into the records and links they imply.

Everything this stage concludes is executed later without interpretation, so
a plan that is wrong is not caught by anything downstream — it is simply
carried out. These tests are therefore mostly about exactness: each action
produces these records and these links and no others.

Two of them are the ones the build plan named from the start. An identical
past record produces a link saying so, with both records left whole. A
changed one produces a new version, a link back to the old, a link to
whatever caused the change, and a written description of what changed.

The last group checks the plan refuses to be built wrong at all — a link
pointing at nothing, a record created twice, records in an order that would
not work — because those are the failures that would otherwise stop halfway
through saving and leave an entry in two halves.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from lumen.pipeline.reconciliation import plan
from lumen.pipeline.reconciliation.contracts import (
    GateRule,
    HistoricalNode,
    NewNodeContent,
)
from lumen.schemas.enums import (
    BookkeepingOperation,
    DecisionStatus,
    ObservationType,
    ReconciliationAction,
)
from lumen.schemas.nodes import BeliefNode, DecisionAuditNode, RollbackPointer
from lumen.schemas.pipeline import (
    GraphWritePlan,
    PlannedEdge,
    PlannedNode,
)

AT = datetime(2026, 6, 11, 20, 0, tzinfo=UTC)


def existing_pattern(node_id: str = "pat_old") -> HistoricalNode:
    return HistoricalNode(
        node_id=node_id,
        node_type="PatternNode",
        preview="Comparing himself to peers",
        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
        row={
            "_label": "PatternNode",
            "node_id": node_id,
            "version": 1,
            "pattern_name": "Comparison spiral",
            "pattern_description": "Comparing himself to peers",
            "domain": "EMOTIONAL",
            "signal_strength": "STANDARD",
            "provenance": "USER_GENERATED",
            "evidence_count": 3,
        },
    )


def existing_belief(node_id: str = "bel_old") -> HistoricalNode:
    return HistoricalNode(
        node_id=node_id,
        node_type="BeliefNode",
        preview="I need solitude to recharge",
        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
        row={
            "_label": "BeliefNode",
            "node_id": node_id,
            "version": 1,
            "belief_statement": "I need solitude to recharge",
            "belief_source_summary": "said so in January",
            "domain": "SELF_CONCEPT",
            "signal_strength": "STANDARD",
            "provenance": "USER_GENERATED",
            "evidence_count": 2,
        },
    )


def context(*records: HistoricalNode, anchor: tuple[str, str] | None = ("sess_1", "SessionNode")):
    return plan.PlanContext(
        at=AT,
        event_date=date(2026, 6, 11),
        history={record.node_id: record for record in records},
        exists=lambda _node_id: False,
        anchor_node_id=anchor[0] if anchor else None,
        anchor_node_type=anchor[1] if anchor else None,
    )


def tables(fragment) -> list[str]:
    return [edge.table for edge in fragment.edges]


def plain_link(source: str = "obs_1", target: str = "pat_missing"):
    """A link carrying nothing but when it was made."""
    from lumen.schemas.edges import LumenEdge

    return LumenEdge(source_node_id=source, target_node_id=target, valid_from=AT)


class TestSayingTwoThingsAreTheSame:
    def test_it_links_the_finding_to_the_record(self, make_settled):
        decision = make_settled(ReconciliationAction.MERGE, confidence=0.91)

        fragment, _ = plan.plan_for(decision, context(existing_pattern()), sequence=1)

        assert "same_as_obs_pat" in tables(fragment)

    def test_nothing_is_collapsed_or_removed(self, make_settled):
        # The common misunderstanding of merging. Both records go on
        # existing with their own history; the link between them is the
        # whole of what merging means, and undoing it leaves both intact.
        decision = make_settled(ReconciliationAction.MERGE, confidence=0.91)

        fragment, _ = plan.plan_for(decision, context(existing_pattern()), sequence=1)

        assert [node.node_type for node in fragment.nodes] == ["DecisionAuditNode"]
        assert fragment.bookkeeping == []

    def test_an_identical_past_record_merges(self, make_item, make_candidate, make_settled):
        # The case the build plan named: the same thing said again.
        item = make_item(
            "Comparing myself to peers and feeling behind",
            candidates=[make_candidate("pat_old", preview="Comparing himself to peers")],
        )
        decision = make_settled(ReconciliationAction.MERGE, item=item, confidence=0.93)

        fragment, audit = plan.plan_for(
            decision, context(existing_pattern()), sequence=1
        )

        assert audit.action is ReconciliationAction.MERGE
        assert audit.target_node_id == "pat_old"
        assert audit.edge_type_created == "same_as_obs_pat"
        assert audit.status is DecisionStatus.ACTIVE


class TestAddingEvidence:
    def test_it_links_and_moves_the_count(self, make_settled):
        decision = make_settled(ReconciliationAction.REINFORCE, confidence=0.85)

        fragment, _ = plan.plan_for(decision, context(existing_pattern()), sequence=1)

        assert "reinforces_obs_pat" in tables(fragment)
        assert [
            (update.operation, update.node_id) for update in fragment.bookkeeping
        ] == [(BookkeepingOperation.RECORD_REINFORCEMENT, "pat_old")]

    def test_the_finding_stays_its_own_occasion(self, make_settled):
        decision = make_settled(ReconciliationAction.REINFORCE, confidence=0.85)

        fragment, _ = plan.plan_for(decision, context(existing_pattern()), sequence=1)

        assert [node.node_type for node in fragment.nodes] == ["DecisionAuditNode"]


class TestSomethingHavingChanged:
    def _decision(self, make_settled, **extra):
        return make_settled(
            ReconciliationAction.EVOLVE,
            target_node_id="bel_old",
            target_type="BeliefNode",
            confidence=0.95,
            delta_description="solitude stopped being the only way he recovers",
            new_node=NewNodeContent(statement="I recharge with people I trust"),
            **extra,
        )

    def test_it_writes_a_new_version_with_the_delta(self, make_settled):
        # The second case the build plan named.
        fragment, audit = plan.plan_for(
            self._decision(make_settled), context(existing_belief()), sequence=1
        )

        versions = [node for node in fragment.nodes if node.node_type == "BeliefNode"]
        assert len(versions) == 1
        assert versions[0].node.version == 2
        assert versions[0].node.previous_version_id == "bel_old"
        assert audit.delta_description.startswith("solitude stopped")

    def test_the_old_version_is_kept_and_marked_past(self, make_settled):
        fragment, _ = plan.plan_for(
            self._decision(make_settled), context(existing_belief()), sequence=1
        )

        assert [
            (update.operation, update.node_id) for update in fragment.bookkeeping
        ] == [(BookkeepingOperation.MARK_SUPERSEDED, "bel_old")]

    def test_it_links_back_to_the_version_it_follows(self, make_settled):
        fragment, _ = plan.plan_for(
            self._decision(make_settled), context(existing_belief()), sequence=1
        )

        assert "evolved_from_bel" in tables(fragment)

    def test_it_always_links_to_what_caused_the_change(self, make_settled):
        # Without this a version chain is a list of edits. With it, it is a
        # story somebody can still read years later.
        fragment, _ = plan.plan_for(
            self._decision(make_settled), context(existing_belief()), sequence=1
        )

        caused_by = [edge for edge in fragment.edges if edge.table == "caused_by_bel_sess"]
        assert [edge.to_node_id for edge in caused_by] == ["sess_1"]

    def test_a_change_with_nothing_behind_it_writes_no_cause(self, make_settled):
        fragment, _ = plan.plan_for(
            self._decision(make_settled),
            context(existing_belief(), anchor=None),
            sequence=1,
        )

        assert not any("caused_by" in table for table in tables(fragment))

    def test_a_change_to_a_record_nobody_read_back_writes_nothing(self, make_settled):
        # A new version is built out of the whole of the old one. Without it
        # there is nothing to build from, and inventing the missing fields
        # would put words in the person's mouth.
        fragment, audit = plan.plan_for(
            self._decision(make_settled), context(), sequence=1
        )

        assert [node.node_type for node in fragment.nodes] == ["DecisionAuditNode"]
        assert audit.edge_type_created is None

    def test_taking_ownership_is_recorded(self, make_settled):
        fragment, audit = plan.plan_for(
            self._decision(make_settled, co_created_origin=True),
            context(existing_belief()),
            sequence=1,
        )

        assert audit.co_created_origin is True


class TestRecordingSomethingNew:
    def test_a_claim_becomes_a_lasting_record_and_is_linked(self, make_item, make_settled):
        decision = make_settled(
            ReconciliationAction.BRANCH,
            item=make_item(observation_type=ObservationType.PATTERN),
            target_node_id=None,
            target_type=None,
            confidence=0.8,
            new_node=NewNodeContent(kind="PATTERN", name="Comparison spiral"),
        )

        fragment, _ = plan.plan_for(decision, context(), sequence=1)

        assert [node.node_type for node in fragment.nodes] == [
            "PatternNode",
            "DecisionAuditNode",
        ]
        assert "branches_to_obs_pat" in tables(fragment)

    def test_the_texture_of_a_day_creates_no_record(self, make_item, make_settled):
        # Still decided, still saved with its entry, still linked to the
        # note of the decision. It simply does not also become a permanent
        # claim about who this person is.
        decision = make_settled(
            ReconciliationAction.BRANCH,
            item=make_item(observation_type=ObservationType.EMOTION),
            target_node_id=None,
            target_type=None,
            confidence=0.8,
        )

        fragment, audit = plan.plan_for(decision, context(), sequence=1)

        assert [node.node_type for node in fragment.nodes] == ["DecisionAuditNode"]
        assert audit.action is ReconciliationAction.BRANCH

    def test_a_returning_question_becomes_a_standing_one(
        self, make_item, make_candidate, make_settled
    ):
        decision = make_settled(
            ReconciliationAction.BRANCH,
            item=make_item(
                "Do I actually want this career?",
                observation_type=ObservationType.OPEN_LOOP,
                candidates=[make_candidate("obs_earlier", node_type="ObservationNode")],
            ),
            target_node_id=None,
            target_type=None,
            confidence=0.8,
        )

        fragment, _ = plan.plan_for(decision, context(), sequence=1)

        assert [node.node_type for node in fragment.nodes] == [
            "OpenLoopNode",
            "DecisionAuditNode",
        ]
        assert "investigated_by" in tables(fragment)


class TestTwoBeliefsHeldAtOnce:
    def _decision(self, make_item, make_settled):
        return make_settled(
            ReconciliationAction.CONTRADICT,
            item=make_item(
                "I thrive when everyone is looking at me",
                observation_type=ObservationType.BELIEF,
            ),
            target_node_id="bel_old",
            target_type="BeliefNode",
            confidence=0.9,
            contradiction_summary="needs solitude and thrives on attention",
            new_node=NewNodeContent(kind="BELIEF", name="Thrives on attention"),
        )

    def test_it_records_the_new_belief_and_the_clash(self, make_item, make_settled):
        fragment, _ = plan.plan_for(
            self._decision(make_item, make_settled), context(existing_belief()), sequence=1
        )

        assert [node.node_type for node in fragment.nodes] == [
            "BeliefNode",
            "ContradictionNode",
            "DecisionAuditNode",
        ]

    def test_both_beliefs_are_joined_to_it(self, make_item, make_settled):
        fragment, _ = plan.plan_for(
            self._decision(make_item, make_settled), context(existing_belief()), sequence=1
        )

        joins = [edge for edge in fragment.edges if edge.table == "contradicts"]
        assert len(joins) == 2
        assert "bel_old" in {edge.to_node_id for edge in joins}

    def test_neither_belief_gives_way(self, make_item, make_settled):
        # The difference from a change. Both are held right now, and forcing
        # a resolution the person has not reached would be the system
        # deciding something about them that they have not.
        fragment, _ = plan.plan_for(
            self._decision(make_item, make_settled), context(existing_belief()), sequence=1
        )

        assert fragment.bookkeeping == []

    def test_the_older_belief_is_left_exactly_as_written(self, make_item, make_settled):
        fragment, _ = plan.plan_for(
            self._decision(make_item, make_settled), context(existing_belief()), sequence=1
        )

        assert not any(
            node.node.node_id == "bel_old" for node in fragment.nodes
        )


class TestTwoThingsBothTrue:
    def test_it_links_them_with_the_tension_written_down(self, make_item, make_settled):
        decision = make_settled(
            ReconciliationAction.DIALECTIC,
            item=make_item(
                "I need to feel appreciated", observation_type=ObservationType.BELIEF
            ),
            target_node_id="bel_old",
            target_type="BeliefNode",
            confidence=0.9,
            tension_summary="both true and pulling against each other",
            new_node=NewNodeContent(kind="BELIEF", name="Needs appreciation"),
        )

        fragment, _ = plan.plan_for(decision, context(existing_belief()), sequence=1)

        tension = [edge for edge in fragment.edges if edge.table == "dialectic_bel_bel"]
        assert len(tension) == 1
        assert tension[0].edge.tension_summary.startswith("both true")

    def test_neither_side_is_superseded(self, make_item, make_settled):
        decision = make_settled(
            ReconciliationAction.DIALECTIC,
            item=make_item(observation_type=ObservationType.BELIEF),
            target_node_id="bel_old",
            target_type="BeliefNode",
            confidence=0.9,
            tension_summary="both true",
            new_node=NewNodeContent(kind="BELIEF", name="Needs appreciation"),
        )

        fragment, _ = plan.plan_for(decision, context(existing_belief()), sequence=1)

        assert fragment.bookkeeping == []


class TestCatchingYourselfMidHabit:
    def test_it_links_with_what_was_interrupted(self, make_settled):
        decision = make_settled(
            ReconciliationAction.REGULATE,
            confidence=0.85,
            regulation_summary="noticed the spiral starting and stopped",
        )

        fragment, _ = plan.plan_for(decision, context(existing_pattern()), sequence=1)

        regulates = [edge for edge in fragment.edges if edge.table == "regulates_obs"]
        assert regulates[0].edge.regulation_summary.startswith("noticed the spiral")

    def test_the_habit_itself_is_untouched(self, make_settled):
        # Noticing a habit once is not the same as no longer having it, and
        # recording it as a change would be flattering and wrong.
        decision = make_settled(
            ReconciliationAction.REGULATE,
            confidence=0.85,
            regulation_summary="caught it",
        )

        fragment, _ = plan.plan_for(decision, context(existing_pattern()), sequence=1)

        assert fragment.bookkeeping == []
        assert [node.node_type for node in fragment.nodes] == ["DecisionAuditNode"]


class TestEveryDecisionIsRecorded:
    @pytest.mark.parametrize(
        ("action", "extra"),
        [
            (ReconciliationAction.MERGE, {}),
            (ReconciliationAction.REINFORCE, {}),
            (ReconciliationAction.BRANCH, {}),
            (ReconciliationAction.REGULATE, {"regulation_summary": "caught it"}),
        ],
    )
    def test_each_action_writes_its_note(self, action, extra, make_settled):
        fragment, audit = plan.plan_for(
            make_settled(action, confidence=0.95, **extra),
            context(existing_pattern()),
            sequence=3,
        )

        assert audit.node_id == "d_2026_06_11_003"
        assert any(node.node.node_id == audit.node_id for node in fragment.nodes)

    def test_the_finding_points_at_its_note(self, make_settled):
        fragment, audit = plan.plan_for(
            make_settled(ReconciliationAction.MERGE), context(existing_pattern()), sequence=1
        )

        decided_by = [edge for edge in fragment.edges if edge.table == "decided_by_obs"]
        assert [edge.to_node_id for edge in decided_by] == [audit.node_id]

    def test_a_session_can_point_at_its_note_too(self, make_item, make_settled):
        decision = make_settled(
            ReconciliationAction.BRANCH,
            item=make_item(node_type="SessionNode", node_id="sess_1"),
            target_node_id=None,
            target_type=None,
            confidence=0.8,
        )

        fragment, _ = plan.plan_for(decision, context(), sequence=1)

        assert "decided_by_sess" in tables(fragment)

    def test_a_decision_that_waits_still_writes_its_note(self, make_settled):
        # An entry where nothing happened and an entry where something was
        # deliberately not done look identical in a graph, and only one of
        # them is waiting on somebody.
        decision = make_settled(ReconciliationAction.MERGE, confidence=0.4).refuse(
            GateRule.BELOW_THRESHOLD
        )

        fragment, audit = plan.plan_for(decision, context(existing_pattern()), sequence=1)

        assert audit.status is DecisionStatus.BELOW_THRESHOLD
        assert [node.node_type for node in fragment.nodes] == ["DecisionAuditNode"]
        assert tables(fragment) == ["decided_by_obs"]

    def test_a_tie_is_recorded_as_waiting_for_a_person(self, make_settled):
        decision = make_settled(ReconciliationAction.AMBIGUOUS).refuse(GateRule.TIE)

        _, audit = plan.plan_for(decision, context(existing_pattern()), sequence=1)

        assert audit.status is DecisionStatus.PENDING_HITL

    def test_the_note_keeps_the_second_reading(self, make_settled):
        # Recorded even when there was no tie, so a run of close calls can
        # be looked at afterwards.
        decision = make_settled(
            ReconciliationAction.MERGE,
            confidence=0.95,
            runner_up=ReconciliationAction.REINFORCE,
            runner_up_confidence=0.60,
        )

        _, audit = plan.plan_for(decision, context(existing_pattern()), sequence=1)

        assert audit.runner_up_action is ReconciliationAction.REINFORCE
        assert audit.confidence_runner_up == 0.60

    def test_how_the_record_was_found_is_recorded(self, make_item, make_candidate, make_settled):
        # "A name matched" and "it reads similarly" are different claims,
        # and the second is much easier to over-trust.
        item = make_item(
            candidates=[make_candidate("pat_old", anchor="NAMED_PERSON")]
        )
        decision = make_settled(ReconciliationAction.MERGE, item=item, confidence=0.9)
        settled = decision.model_copy(
            update={
                "retrieval_source": item.candidates[0].retrieval_source,
                "anchor_type": item.candidates[0].structural_anchor_type,
                "anchor_value": item.candidates[0].structural_anchor_value,
            }
        )

        _, audit = plan.plan_for(settled, context(existing_pattern()), sequence=1)

        assert audit.structural_anchor_type is not None
        assert audit.structural_anchor_value == "Alex"


class TestReversingADecision:
    def test_the_note_says_which_link_to_undo(self, make_settled):
        # Links have no identifier of their own in the graph, so the handle
        # has to name both ends and the table they sit in.
        _, audit = plan.plan_for(
            make_settled(ReconciliationAction.MERGE, confidence=0.95),
            context(existing_pattern()),
            sequence=1,
        )

        assert audit.rollback_pointer.edge_to_invalidate == (
            "same_as_obs_pat:obs_new_1->pat_old"
        )

    def test_it_says_what_to_look_at_again(self, make_settled):
        _, audit = plan.plan_for(
            make_settled(ReconciliationAction.MERGE, confidence=0.95),
            context(existing_pattern()),
            sequence=1,
        )

        assert audit.rollback_pointer.nodes_to_requeue == ["obs_new_1"]

    def test_a_decision_with_no_link_still_has_a_handle(self, make_settled):
        decision = make_settled(ReconciliationAction.AMBIGUOUS).refuse(GateRule.TIE)

        _, audit = plan.plan_for(decision, context(), sequence=1)

        assert audit.rollback_pointer.edge_to_invalidate == "none:d_2026_06_11_001"


class TestAPlanRefusesToBeBuiltWrong:
    def _audit_node(self, node_id: str = "d_1") -> DecisionAuditNode:
        return DecisionAuditNode(
            node_id=node_id,
            created_at=AT,
            action=ReconciliationAction.BRANCH,
            source_node_id="obs_1",
            confidence=0.8,
            model_used="fake",
            model_role="LIGHTWEIGHT",
            candidate_retrieval_source="SEMANTIC",
            status=DecisionStatus.ACTIVE,
            rollback_pointer=RollbackPointer(edge_to_invalidate="none:d_1"),
        )

    def _belief(self, node_id: str, **extra) -> BeliefNode:
        return BeliefNode(
            node_id=node_id,
            created_at=AT,
            valid_from=AT,
            last_reinforced_at=AT,
            belief_statement="a belief",
            belief_source_summary="from somewhere",
            domain="SELF_CONCEPT",
            signal_strength="STANDARD",
            provenance="USER_GENERATED",
            **extra,
        )

    def test_a_link_pointing_at_nothing_is_refused(self):
        # A plan is carried out straight through. A dangling link would stop
        # halfway and leave an entry half saved.
        with pytest.raises(ValidationError, match="unknown record"):
            GraphWritePlan(
                edges=[
                    PlannedEdge(
                        logical_type="same_as",
                        table="same_as_obs_pat",
                        from_node_id="obs_1",
                        to_node_id="pat_missing",
                        edge=plain_link(),
                    )
                ],
                existing_node_ids=frozenset({"obs_1"}),
            )

    def test_a_link_to_something_the_plan_creates_is_fine(self):
        plan_ = GraphWritePlan(
            nodes=[PlannedNode(node_type="DecisionAuditNode", node=self._audit_node())],
            edges=[
                PlannedEdge(
                    logical_type="decided_by",
                    table="decided_by_obs",
                    from_node_id="obs_1",
                    to_node_id="d_1",
                    edge=plain_link(target="d_1"),
                )
            ],
            existing_node_ids=frozenset({"obs_1"}),
        )

        assert len(plan_.edges) == 1

    def test_creating_the_same_record_twice_is_refused(self):
        with pytest.raises(ValidationError, match="more than once"):
            GraphWritePlan(
                nodes=[
                    PlannedNode(node_type="DecisionAuditNode", node=self._audit_node()),
                    PlannedNode(node_type="DecisionAuditNode", node=self._audit_node()),
                ]
            )

    def test_records_out_of_order_are_refused(self):
        # A new version naming the one it follows has to come after it.
        with pytest.raises(ValidationError, match="creates later"):
            GraphWritePlan(
                nodes=[
                    PlannedNode(
                        node_type="BeliefNode",
                        node=self._belief("bel_v2", version=2, previous_version_id="bel_v1"),
                    ),
                    PlannedNode(node_type="BeliefNode", node=self._belief("bel_v1")),
                ]
            )

    def test_naming_a_record_that_already_exists_is_fine(self):
        # Most references are to records the plan does not create at all.
        plan_ = GraphWritePlan(
            nodes=[
                PlannedNode(
                    node_type="BeliefNode",
                    node=self._belief("bel_v2", version=2, previous_version_id="bel_v1"),
                )
            ],
            existing_node_ids=frozenset({"bel_v1"}),
        )

        assert len(plan_.nodes) == 1

    def test_a_links_own_columns_leave_out_its_two_ends(self):
        # The ends are how a link is attached, not something it carries. A
        # stored copy of them could disagree with where the link actually
        # sits.
        edge = PlannedEdge(
            logical_type="decided_by",
            table="decided_by_obs",
            from_node_id="obs_1",
            to_node_id="d_1",
            edge=plain_link(target="d_1"),
        )

        assert "source_node_id" not in edge.properties()
        assert "valid_from" in edge.properties()


class TestWhenADecisionCannotBeCarriedOut:
    """
    What happens when an action is asked for and there is no way to record
    it. Every one of these ends the same way — the decision does less rather
    than the entry stopping halfway through being saved.
    """

    def test_a_clash_with_nothing_to_clash_with_writes_nothing(
        self, make_item, make_settled
    ):
        decision = make_settled(
            ReconciliationAction.CONTRADICT,
            item=make_item(observation_type=ObservationType.BELIEF),
            target_node_id=None,
            target_type=None,
            confidence=0.9,
            contradiction_summary="a clash",
        )

        fragment, audit = plan.plan_for(decision, context(), sequence=1)

        assert [node.node_type for node in fragment.nodes] == ["DecisionAuditNode"]
        assert audit.edge_type_created is None

    def test_a_tension_needing_a_record_that_cannot_be_made_writes_nothing(
        self, make_item, make_settled
    ):
        decision = make_settled(
            ReconciliationAction.DIALECTIC,
            item=make_item(observation_type=ObservationType.EMOTION),
            target_node_id="bel_old",
            target_type="BeliefNode",
            confidence=0.9,
            tension_summary="both true",
        )

        fragment, _ = plan.plan_for(decision, context(existing_belief()), sequence=1)

        assert [node.node_type for node in fragment.nodes] == ["DecisionAuditNode"]

    def test_a_link_with_no_target_is_simply_not_made(self, make_settled):
        decision = make_settled(
            ReconciliationAction.MERGE,
            target_node_id=None,
            target_type=None,
            confidence=0.95,
        )

        fragment, audit = plan.plan_for(decision, context(), sequence=1)

        assert audit.edge_type_created is None
        assert tables(fragment) == ["decided_by_obs"]

    def test_an_unsupported_pairing_loses_the_link_not_the_entry(self, make_settled):
        # Resolving which table a link belongs in happens while planning
        # precisely so this is a decision that did less, rather than a save
        # that stops with half an entry already written.
        decision = make_settled(
            ReconciliationAction.MERGE,
            target_node_id="les_old",
            target_type="LessonNode",
            confidence=0.95,
        )

        fragment, audit = plan.plan_for(decision, context(), sequence=1)

        assert audit.edge_type_created is None
        assert tables(fragment) == ["decided_by_obs"]

    def test_a_tie_writes_only_its_note(self, make_settled):
        decision = make_settled(ReconciliationAction.AMBIGUOUS).refuse(GateRule.TIE)

        fragment, _ = plan.plan_for(decision, context(), sequence=1)

        assert [node.node_type for node in fragment.nodes] == ["DecisionAuditNode"]


class TestEveryActionHasABuilder:
    def test_all_eight_are_covered(self):
        # A ninth action added later without a builder should fail here,
        # rather than quietly writing nothing at all.
        from lumen.pipeline.reconciliation.plan import _BUILDERS

        assert set(_BUILDERS) == set(ReconciliationAction)

    def test_a_tie_that_somehow_reaches_the_builders_writes_only_its_note(
        self, make_settled
    ):
        decision = make_settled(ReconciliationAction.AMBIGUOUS, confidence=0.9)

        fragment, _ = plan.plan_for(decision, context(), sequence=1)

        assert [node.node_type for node in fragment.nodes] == ["DecisionAuditNode"]
        assert tables(fragment) == ["decided_by_obs"]


class TestHowAWaitingDecisionIsRecorded:
    def test_a_tie_is_marked_as_waiting_even_arriving_another_way(self, make_settled):
        # A tie never acts on its own by rule, so it has to reach that state
        # however it got here — not only by the route that normally sets it.
        decision = make_settled(ReconciliationAction.AMBIGUOUS, confidence=0.9)

        _, audit = plan.plan_for(decision, context(), sequence=1)

        assert audit.status is DecisionStatus.PENDING_HITL

    def test_a_tie_found_by_the_check_is_marked_the_same_way(self, make_settled):
        decision = make_settled(ReconciliationAction.AMBIGUOUS).refuse(GateRule.TIE)

        _, audit = plan.plan_for(decision, context(), sequence=1)

        assert audit.status is DecisionStatus.PENDING_HITL

    def test_anything_else_held_back_is_marked_as_not_confident_enough(
        self, make_settled
    ):
        decision = make_settled(ReconciliationAction.MERGE, confidence=0.4).refuse(
            GateRule.BELOW_THRESHOLD
        )

        _, audit = plan.plan_for(decision, context(existing_pattern()), sequence=1)

        assert audit.status is DecisionStatus.BELOW_THRESHOLD
