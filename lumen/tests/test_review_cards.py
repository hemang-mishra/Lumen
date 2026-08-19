"""
Tests for turning a saved question into something answerable in seconds.

A card is judged on whether somebody could answer it without opening
anything else: the finding in its own words, the earlier record in its own
words, and what each button would actually do.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from lumen.operational.enums import HitlItemStatus
from lumen.operational.schemas import HitlQueueItemRecord
from lumen.review import cards
from lumen.review.contracts import ResolutionChoice
from lumen.schemas.enums import (
    HitlEntryType,
    LifecycleNodeStatus,
    ReconciliationAction,
    SignalStrength,
)


@pytest.fixture
def rows(sample_pattern):
    """The records behind a card, as the graph returns them."""
    row = sample_pattern.to_graph_dict()
    row["_label"] = "PatternNode"
    return {sample_pattern.node_id: row}


@pytest.fixture
def item(moment):
    """One waiting question, as the queue stores it."""

    def _build(
        *,
        entry_type=HitlEntryType.BELOW_THRESHOLD,
        snooze_count: int = 0,
        last_snoozed_at=None,
        snoozed_until=None,
        audit_id: str = "d_2026_06_11_01_001",
    ):
        return HitlQueueItemRecord(
            id=f"hitl_{audit_id}",
            user_id="tester",
            audit_node_id=audit_id,
            entry_type=entry_type,
            signal_strength=SignalStrength.HIGH,
            status=HitlItemStatus.PENDING_HITL,
            episode_id="ep_1",
            recommended_action=ReconciliationAction.REINFORCE,
            created_at=moment,
            snooze_count=snooze_count,
            last_snoozed_at=last_snoozed_at,
            snoozed_until=snoozed_until,
        )

    return _build


def build(proposal, item, rows, *, now, summaries=None, days: int = 7):
    """Assemble one card, with the reading arguments filled in."""
    return cards.build_card(
        item,
        proposal,
        rows=rows,
        episode_summaries=summaries or {},
        now=now,
        auto_resolve_days=days,
    )


class TestWhatACardOffers:
    """The buttons follow from why the item is waiting."""

    def test_a_low_confidence_item_offers_two_answers(
        self, make_proposal, item, rows, moment
    ):
        card = build(make_proposal(), item(), rows, now=moment)

        assert [option.choice for option in card.options] == [
            ResolutionChoice.APPROVE,
            ResolutionChoice.REJECT,
        ]

    def test_a_tie_offers_three(self, make_proposal, item, rows, moment, sample_pattern):
        proposal = make_proposal(
            ReconciliationAction.REINFORCE,
            runner_up=ReconciliationAction.MERGE,
            runner_up_target=sample_pattern.node_id,
        )

        card = build(
            proposal, item(entry_type=HitlEntryType.AMBIGUOUS_TIE), rows, now=moment
        )

        assert [option.choice for option in card.options] == [
            ResolutionChoice.ACTION_A,
            ResolutionChoice.ACTION_B,
            ResolutionChoice.CREATE_NEW,
        ]

    def test_a_tie_whose_second_reading_was_lost_offers_two(
        self, make_proposal, item, rows, moment
    ):
        # The second reading named a record the search never surfaced, so it
        # was never kept. The card shows what it can actually carry out.
        proposal = make_proposal(
            ReconciliationAction.REINFORCE,
            runner_up=ReconciliationAction.MERGE,
            runner_up_target="pat_never_seen",
            entry_type=HitlEntryType.AMBIGUOUS_TIE,
        )

        card = build(
            proposal, item(entry_type=HitlEntryType.AMBIGUOUS_TIE), rows, now=moment
        )

        assert [option.choice for option in card.options] == [
            ResolutionChoice.ACTION_A,
            ResolutionChoice.CREATE_NEW,
        ]

    def test_every_button_says_what_it_would_do(
        self, make_proposal, item, rows, moment
    ):
        card = build(make_proposal(), item(), rows, now=moment)

        for option in card.options:
            assert option.label
            assert option.action is not None

    def test_a_button_that_records_nothing_says_so(
        self, make_proposal, item, rows, moment, make_item
    ):
        from lumen.schemas.enums import ObservationType

        finding = make_item(
            text="the coffee was cold", observation_type=ObservationType.EMOTION
        )
        card = build(make_proposal(item=finding), item(), rows, now=moment)

        reject = next(
            option
            for option in card.options
            if option.choice is ResolutionChoice.REJECT
        )
        assert reject.writes_nothing


class TestSayingNoToRecordingSomethingOnItsOwn:
    """
    Turning down "record this on its own" cannot mean recording it on its own.

    The fallback answer is normally BRANCH — "no, it is something separate".
    When the recommendation is already BRANCH the two collapse, and offering
    both asks somebody to choose between a thing and itself.
    """

    def test_the_second_button_becomes_a_refusal(
        self, make_proposal, item, rows, moment
    ):
        proposal = make_proposal(ReconciliationAction.BRANCH, target_node_id="")

        card = build(proposal, item(), rows, now=moment)

        reject = card.options[1]
        assert reject.declines is True
        assert reject.writes_nothing is True
        assert "leave it with the entry" in reject.label

    def test_it_stays_a_real_action_when_there_is_one(
        self, make_proposal, item, rows, moment
    ):
        # Against "this is the same as that", saying no genuinely does mean
        # recording it separately, and that writes something.
        proposal = make_proposal(ReconciliationAction.MERGE)

        card = build(proposal, item(), rows, now=moment)

        reject = card.options[1]
        assert reject.declines is False

    def test_a_refusal_offers_no_record_to_act_on(
        self, make_proposal, item, rows, moment
    ):
        # There is nothing to point at: the whole answer is that nothing
        # happens.
        proposal = make_proposal(ReconciliationAction.BRANCH, target_node_id="")

        card = build(proposal, item(), rows, now=moment)

        assert card.options[1].target is None

    def test_a_tie_says_the_same_thing_in_its_own_words(
        self, make_proposal, item, rows, moment
    ):
        proposal = make_proposal(
            ReconciliationAction.BRANCH,
            target_node_id="",
            runner_up=ReconciliationAction.BRANCH,
            entry_type=HitlEntryType.AMBIGUOUS_TIE,
        )

        card = build(
            proposal, item(entry_type=HitlEntryType.AMBIGUOUS_TIE), rows, now=moment
        )

        neither = card.options[-1]
        assert neither.choice is ResolutionChoice.CREATE_NEW
        assert neither.declines is True
        assert "leave it with the entry" in neither.label


class TestWhatACardShows:
    """Everything needed for a judgement, without opening anything else."""

    def test_the_finding_is_shown_in_its_own_words(
        self, make_proposal, item, rows, moment, make_item
    ):
        finding = make_item(text="I said yes before I had thought about it.")
        card = build(make_proposal(item=finding), item(), rows, now=moment)

        assert "said yes" in card.source_text

    def test_the_earlier_record_is_shown_in_its_own_words(
        self, make_proposal, item, rows, moment, sample_pattern
    ):
        card = build(make_proposal(), item(), rows, now=moment)

        approve = card.options[0]
        assert approve.target is not None
        assert approve.target.node_id == sample_pattern.node_id
        assert approve.target.text

    def test_a_record_that_cannot_be_read_is_still_named(
        self, make_proposal, item, moment
    ):
        # "This points at something I cannot read" is information. A
        # silently missing candidate is not.
        card = build(make_proposal(), item(), {}, now=moment)

        assert card.options[0].target is not None
        assert card.options[0].target.node_type == "unknown"

    def test_the_entry_it_came_from_is_shown(
        self, make_proposal, item, rows, moment
    ):
        card = build(
            make_proposal(),
            item(),
            rows,
            now=moment,
            summaries={"ep_1": "A hard conversation about work."},
        )

        assert card.episode_summary == "A hard conversation about work."

    def test_the_question_is_stated_plainly(self, make_proposal, item, rows, moment):
        low = build(make_proposal(), item(), rows, now=moment)
        tie = build(
            make_proposal(),
            item(entry_type=HitlEntryType.AMBIGUOUS_TIE),
            rows,
            now=moment,
        )

        assert low.question != tie.question
        assert low.question and tie.question


class TestAges:
    """How long a question has waited, and when it settles itself."""

    def test_the_age_is_counted_from_when_it_was_raised(
        self, make_proposal, item, rows, moment
    ):
        card = build(make_proposal(), item(), rows, now=moment + timedelta(days=3))

        assert card.age_days == 3

    def test_an_untouched_item_never_settles_itself(
        self, make_proposal, item, rows, moment
    ):
        # Deferring something is evidence it was seen. Never opening it is
        # not, and acting on silence would decide things nobody agreed to.
        card = build(make_proposal(), item(), rows, now=moment)

        assert card.auto_resolves_at is None

    def test_a_deferred_item_shows_when_it_settles_itself(
        self, make_proposal, item, rows, moment
    ):
        card = build(
            make_proposal(),
            item(snooze_count=1, last_snoozed_at=moment),
            rows,
            now=moment,
        )

        assert card.auto_resolves_at == moment + timedelta(days=7)

    def test_a_deferred_item_shows_when_it_comes_back(
        self, make_proposal, item, rows, moment
    ):
        returns = moment + timedelta(hours=24)
        card = build(
            make_proposal(),
            item(snooze_count=1, last_snoozed_at=moment, snoozed_until=returns),
            rows,
            now=moment,
        )

        assert card.snoozed_until == returns


class TestGoingStale:
    """A card whose recommendation has been overtaken says so."""

    def test_a_replaced_record_makes_the_card_stale(
        self, make_proposal, item, rows, moment, sample_pattern
    ):
        rows[sample_pattern.node_id]["status"] = LifecycleNodeStatus.SUPERSEDED.value

        card = build(make_proposal(), item(), rows, now=moment)

        assert card.stale
        assert "newer version" in card.stale_reason

    def test_a_vanished_record_makes_the_card_stale(
        self, make_proposal, item, moment
    ):
        card = build(make_proposal(), item(), {}, now=moment)

        assert card.stale

    def test_an_answer_needing_no_record_is_never_stale(
        self, make_proposal, item, moment
    ):
        proposal = make_proposal(ReconciliationAction.BRANCH, target_node_id="")

        card = build(proposal, item(), {}, now=moment)

        assert not card.stale

    def test_an_unversioned_record_is_not_treated_as_stale(
        self, make_proposal, item, rows, moment, sample_pattern
    ):
        # Not everything in the graph is versioned. Treating a record with
        # no lifecycle as replaced would make every card look stale.
        rows[sample_pattern.node_id].pop("status", None)

        card = build(make_proposal(), item(), rows, now=moment)

        assert not card.stale


class TestAQuestionNobodyCanAnswer:
    """
    It still has to be understandable, or it cannot even be withdrawn.

    A card built only from what the queue row stored is a row of identifiers
    and a machine-written summary. Nobody can judge that, so the row's
    pointers are followed into the graph like any other card's.
    """

    def build(self, item, rows, moment, summaries=None):
        return cards.build_unanswerable_card(
            item,
            rows=rows,
            episode_summaries=summaries or {},
            now=moment,
            auto_resolve_days=7,
            reason="nothing recorded to carry out",
        )

    def test_it_shows_the_finding_in_its_own_words(
        self, item, moment, sample_observation, graph_store
    ):
        row = sample_observation.to_graph_dict()
        row["_label"] = "ObservationNode"
        waiting = item()
        waiting = waiting.model_copy(
            update={"observation_id": sample_observation.node_id}
        )

        card = self.build(waiting, {sample_observation.node_id: row}, moment)

        assert card.source_text == sample_observation.content

    def test_it_never_shows_the_machinery_as_the_finding(self, item, moment):
        # The queue's own summary reads "BRANCH against obs_… held back:
        # BELOW_THRESHOLD", which describes the system rather than the person.
        waiting = item().model_copy(
            update={"context_summary": "BRANCH against obs_1 held back: X"}
        )

        card = self.build(waiting, {}, moment)

        assert "held back" not in card.source_text

    def test_it_shows_what_the_finding_was_weighed_against(
        self, item, rows, moment, sample_pattern
    ):
        waiting = item().model_copy(
            update={"candidate_a_node_id": sample_pattern.node_id}
        )

        card = self.build(waiting, rows, moment)

        assert card.compared_against is not None
        assert card.compared_against.text

    def test_it_shows_which_entry_it_came_from(self, item, moment):
        card = self.build(item(), {}, moment, summaries={"ep_1": "A hard day."})

        assert card.episode_summary == "A hard day."

    def test_it_shows_what_the_system_was_leaning_towards(self, item, moment):
        waiting = item().model_copy(update={"confidence_a": 0.4})

        card = self.build(waiting, {}, moment)

        assert card.recommended_action is ReconciliationAction.REINFORCE
        assert card.recommended_confidence == pytest.approx(0.4)

    def test_it_offers_no_answers_and_says_why(self, item, moment):
        card = self.build(item(), {}, moment)

        assert card.options == []
        assert card.answerable is False
        assert card.unanswerable_reason


class TestReadingTheGraphOnce:
    """A page of cards costs one round of reads, not one per card."""

    def test_the_records_a_bare_queue_row_points_at_are_gathered_too(
        self, item, sample_pattern
    ):
        waiting = item().model_copy(
            update={
                "observation_id": "obs_1",
                "candidate_a_node_id": sample_pattern.node_id,
            }
        )

        wanted = cards.wanted_for_items([waiting])

        assert wanted == sorted(["obs_1", sample_pattern.node_id])

    def test_every_record_a_page_needs_is_gathered_up(
        self, make_proposal, sample_pattern
    ):
        proposal = make_proposal(
            ReconciliationAction.REINFORCE,
            runner_up=ReconciliationAction.MERGE,
            runner_up_target=sample_pattern.node_id,
        )

        wanted = cards.wanted_node_ids([proposal])

        assert sample_pattern.node_id in wanted
        assert proposal.source_node_id in wanted

    def test_nothing_is_asked_for_when_there_is_nothing_to_ask(self, graph_store):
        assert cards.read_rows([], graph=graph_store) == {}

    def test_a_failed_read_does_not_stop_the_queue_opening(self, make_proposal):
        # A card missing the earlier wording is worse than one with it, and
        # far better than a queue that will not open at all.
        class Broken:
            def get_nodes_by_ids(self, _ids):
                raise RuntimeError("the graph is down")

        assert cards.read_rows(["pat_1"], graph=Broken()) == {}

    def test_episode_summaries_come_back_by_identifier(
        self, graph_store, sample_episode
    ):
        graph_store.write_node("EpisodeNode", sample_episode)

        summaries = cards.read_episode_summaries(
            [sample_episode.node_id, ""], graph=graph_store
        )

        assert summaries[sample_episode.node_id] == sample_episode.episode_summary
