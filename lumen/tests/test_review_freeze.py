"""
Tests for keeping a held-back decision so it can be carried out later.

The thing being checked throughout is that what was saved is genuinely
enough to write with. A proposal that describes an answer without being able
to produce it is exactly the failure this module exists to prevent, and it
would not show up until somebody tried to answer a card days later.
"""

from __future__ import annotations

import pytest

from lumen.pipeline.reconciliation.contracts import GateRule
from lumen.pipeline.reconciliation.freeze import freeze
from lumen.schemas.enums import HitlEntryType, ReconciliationAction
from lumen.schemas.pipeline import FrozenProposal

AUDIT_ID = "d_2026_06_11_01_001"


class TestWhatGetsKept:
    """Every held-back decision saves the answers it could be given."""

    def test_the_recommendation_is_kept_with_its_writing(self, make_proposal):
        proposal = make_proposal(ReconciliationAction.REINFORCE)

        assert proposal.primary.action is ReconciliationAction.REINFORCE
        # Reinforcing is a link plus a count on the record it points at, so a
        # saved one that carried neither would answer to nothing.
        assert proposal.primary.edges
        assert proposal.primary.bookkeeping

    def test_standing_alone_is_always_kept(self, make_proposal):
        # It is the answer to "no, this is something else" and the one an
        # unanswered item eventually settles on, so it has to exist whatever
        # the recommendation was.
        proposal = make_proposal(ReconciliationAction.MERGE)

        assert proposal.fallback.action is ReconciliationAction.BRANCH
        assert proposal.fallback.target_node_id is None

    def test_standing_alone_is_kept_even_when_it_was_the_recommendation(
        self, make_proposal
    ):
        proposal = make_proposal(ReconciliationAction.BRANCH, target_node_id="")

        assert proposal.primary.action is ReconciliationAction.BRANCH
        assert proposal.fallback.action is ReconciliationAction.BRANCH

    def test_the_finding_is_kept_in_its_own_words(self, make_proposal, make_item):
        item = make_item(text="I always volunteer before I have thought about it.")
        proposal = make_proposal(item=item)

        # Kept so a card can be drawn without reading the graph at all.
        assert "volunteer" in proposal.source_text

    def test_where_it_came_from_is_kept(self, make_proposal):
        proposal = make_proposal()

        assert proposal.audit_node_id == AUDIT_ID
        assert proposal.episode_id
        assert proposal.episode_index == 1


class TestTheSecondReading:
    """A tie is a question with two answers, and both have to be saved."""

    def test_a_tie_keeps_its_second_reading(self, make_proposal, sample_pattern):
        proposal = make_proposal(
            ReconciliationAction.REINFORCE,
            runner_up=ReconciliationAction.MERGE,
            runner_up_target=sample_pattern.node_id,
        )

        assert proposal.runner_up is not None
        assert proposal.runner_up.action is ReconciliationAction.MERGE
        assert proposal.runner_up.target_node_id == sample_pattern.node_id

    def test_the_second_reading_gets_its_own_confidence(
        self, make_proposal, sample_pattern
    ):
        proposal = make_proposal(
            ReconciliationAction.REINFORCE,
            confidence=0.9,
            runner_up=ReconciliationAction.MERGE,
            runner_up_target=sample_pattern.node_id,
        )

        assert proposal.primary.confidence == pytest.approx(0.9)
        assert proposal.runner_up.confidence == pytest.approx(0.5)

    def test_nothing_is_kept_when_there_was_no_second_reading(self, make_proposal):
        proposal = make_proposal(ReconciliationAction.REINFORCE)

        assert proposal.runner_up is None

    def test_a_second_reading_naming_an_unknown_record_is_dropped(
        self, make_proposal
    ):
        # A reading pointing at something the search never surfaced could not
        # have been acted on when it was fresh. Offering it later would be
        # worse, not better.
        proposal = make_proposal(
            ReconciliationAction.REINFORCE,
            runner_up=ReconciliationAction.MERGE,
            runner_up_target="pat_never_seen",
        )

        assert proposal.runner_up is None

    def test_a_second_reading_needing_no_record_is_kept(self, make_proposal):
        proposal = make_proposal(
            ReconciliationAction.REINFORCE,
            runner_up=ReconciliationAction.BRANCH,
            runner_up_target=None,
        )

        assert proposal.runner_up is not None
        assert proposal.runner_up.action is ReconciliationAction.BRANCH


class TestEveryAction:
    """Each of the eight actions saves something that can be replayed."""

    @pytest.mark.parametrize(
        "action",
        [
            ReconciliationAction.MERGE,
            ReconciliationAction.REINFORCE,
            ReconciliationAction.EVOLVE,
            ReconciliationAction.BRANCH,
            ReconciliationAction.CONTRADICT,
            ReconciliationAction.DIALECTIC,
            ReconciliationAction.REGULATE,
            ReconciliationAction.AMBIGUOUS,
        ],
    )
    def test_freezing_produces_something_replayable(self, make_proposal, action):
        proposal = make_proposal(
            action,
            delta_description="it used to be about proving myself",
            contradiction_summary="both cannot be true",
            tension_summary="held together on purpose",
            regulation_summary="interrupted deliberately",
        )

        assert proposal.primary.action is action
        # A saved answer is a real piece of writing, so it must survive being
        # stored as text and read back — which is the only way it is ever
        # used.
        again = FrozenProposal.model_validate_json(proposal.model_dump_json())
        assert again == proposal

    def test_evolving_keeps_the_new_wording(self, make_proposal):
        # This is the case the whole mechanism exists for. The new version of
        # a belief is worked out while deciding and would otherwise be lost.
        proposal = make_proposal(
            ReconciliationAction.EVOLVE,
            delta_description="it used to be about proving myself",
        )

        assert proposal.primary.nodes, "the next version was not kept"
        assert proposal.primary.delta_description

    def test_an_action_that_writes_nothing_says_so(self, make_proposal):
        proposal = make_proposal(ReconciliationAction.AMBIGUOUS)

        assert proposal.primary.writes_nothing


class TestRoundTripping:
    """A saved proposal is only worth anything if it reads back intact."""

    def test_records_and_links_survive_being_stored(
        self, make_proposal, sample_pattern
    ):
        proposal = make_proposal(
            ReconciliationAction.MERGE,
            runner_up=ReconciliationAction.REINFORCE,
            runner_up_target=sample_pattern.node_id,
        )

        again = FrozenProposal.model_validate_json(proposal.model_dump_json())

        assert again.primary.edges[0].table == proposal.primary.edges[0].table
        assert again.runner_up is not None

        # Read back as the kind of link it really is, not as the shared base
        # type — a merge link that forgot which decision made it is a link
        # nothing can trace or undo.
        restored = again.primary.edges[0].restored()
        assert restored.edge.decision_id == AUDIT_ID
        assert type(restored.edge) is type(proposal.primary.edges[0].restored().edge)

    def test_how_the_record_was_found_is_carried(self, make_proposal):
        proposal = make_proposal()

        # A fact about the search, not about who eventually answers, so it
        # has to reach whatever note records the answer.
        assert proposal.primary.retrieval_source is not None


class TestTheStageKeepsIt:
    """The pipeline saves a proposal for everything it holds back."""

    def test_an_escalation_carries_what_it_would_have_written(
        self, make_settled, plan_context, historical, sample_pattern
    ):
        from lumen.pipeline.reconciliation import stage

        known = historical(sample_pattern)
        decision = make_settled(
            ReconciliationAction.REINFORCE, target_node_id=known.node_id
        ).refuse(GateRule.BELOW_THRESHOLD)

        proposal = stage._frozen(
            decision,
            plan_context(history={known.node_id: known}),
            AUDIT_ID,
            HitlEntryType.BELOW_THRESHOLD,
        )

        assert proposal is not None
        assert proposal.audit_node_id == AUDIT_ID

    def test_a_failure_to_keep_it_does_not_lose_the_entry(
        self, make_settled, plan_context, monkeypatch
    ):
        # Losing the ability to answer one question later is bad. Losing the
        # whole entry because of it would be worse.
        from lumen.pipeline.reconciliation import stage

        def explode(*_args, **_kwargs):
            raise RuntimeError("no")

        monkeypatch.setattr(stage, "freeze", explode)
        decision = make_settled().refuse(GateRule.BELOW_THRESHOLD)

        assert (
            stage._frozen(
                decision, plan_context(), AUDIT_ID, HitlEntryType.BELOW_THRESHOLD
            )
            is None
        )


class TestTheSummaries:
    """Each saved answer says in plain words what taking it would mean."""

    def test_every_answer_explains_itself(self, make_proposal):
        proposal = make_proposal(ReconciliationAction.MERGE)

        assert proposal.primary.summary
        assert proposal.fallback.summary == "record this as its own separate thing"

    @pytest.mark.parametrize(
        "action",
        [
            ReconciliationAction.MERGE,
            ReconciliationAction.EVOLVE,
            ReconciliationAction.CONTRADICT,
        ],
    )
    def test_the_summary_names_what_is_being_acted_on(self, make_proposal, action):
        proposal = make_proposal(
            action,
            delta_description="changed",
            contradiction_summary="both cannot be true",
        )

        assert proposal.primary.target_node_id in proposal.primary.summary


def test_freeze_is_pure(make_settled, plan_context, historical, sample_pattern):
    """Freezing the same decision twice gives the same thing both times."""
    known = historical(sample_pattern)
    context = plan_context(history={known.node_id: known})
    decision = make_settled(
        ReconciliationAction.REINFORCE, target_node_id=known.node_id
    ).refuse(GateRule.BELOW_THRESHOLD)

    first = freeze(
        decision, context, audit_id=AUDIT_ID, entry_type=HitlEntryType.BELOW_THRESHOLD
    )
    second = freeze(
        decision, context, audit_id=AUDIT_ID, entry_type=HitlEntryType.BELOW_THRESHOLD
    )

    assert first == second
