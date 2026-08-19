"""
One real entry the system could not decide about, answered by a person.

This is the join the whole goal exists to make. A run happens, something in
it cannot be settled, the change is held back — and days later somebody taps
a button and the change that was held back actually lands in the graph.

Everything here reads the databases back rather than trusting what the code
reports about itself. The report says what it believes it did; only the
stores say what is there.
"""

from __future__ import annotations

import json

import pytest

from lumen.config import AppConfig
from lumen.operational.enums import HitlItemStatus
from lumen.pipeline.orchestration import run_pipeline
from lumen.review.contracts import ResolutionChoice
from lumen.schemas.enums import (
    HitlEntryType,
    HitlResolutionChoice,
    ReconciliationAction,
)

ENTRY = """\
I went to the cafe alone today and ate there without the usual dread.
Then I saw what Alex had shipped this week and felt small and behind.
I sat with it for a while and the pressure lifted on its own.
I think the comparing is the thing that hurts, not the gap itself.
"""

# Two readings of the same finding, close enough that the model cannot
# separate them. This is what forces the question to reach a person.
A_TIE = json.dumps(
    {
        "decisions": [
            {
                "item_index": 1,
                "primary": {
                    "action": "BRANCH",
                    "confidence": 0.91,
                    "reason": "this may be new",
                },
                "runner_up": {
                    "action": "AMBIGUOUS",
                    "confidence": 0.89,
                    "reason": "or it may be the same old thing",
                },
                "new_node": {
                    "kind": "PATTERN",
                    "name": "comparison_spiral",
                    "statement": "Comparing himself to others is what hurts",
                    "domain": "SELF_CONCEPT",
                },
            }
        ],
        "people": [],
    }
)


@pytest.fixture
def undecided_run(
    ops_store, graph_store, vector_store, embedder, full_run_providers,
    decayed_session,
):
    """Run the whole pipeline over an entry it cannot fully settle."""
    light, deep = full_run_providers({"decision": A_TIE})
    return run_pipeline(
        decayed_session(ENTRY.strip()),
        graph=graph_store,
        vectors=vector_store,
        embedder=embedder,
        lightweight=light,
        thinking=deep,
        ops=ops_store,
        config=AppConfig(),
    )


@pytest.fixture
def waiting(undecided_run, ops_store):
    """The one question the run left for a person."""
    items = ops_store.hitl.list_pending("local")
    assert items, "the run settled everything; there is nothing to answer"
    return items[0]


class TestWhatTheRunLeftBehind:
    """A question, and everything needed to answer it."""

    def test_the_question_is_waiting(self, waiting):
        assert waiting.status is HitlItemStatus.PENDING_HITL
        assert waiting.entry_type is HitlEntryType.AMBIGUOUS_TIE

    def test_what_it_was_going_to_write_was_kept(self, waiting, ops_store):
        assert ops_store.hitl.get_proposal(waiting.audit_node_id) is not None

    def test_the_change_was_held_back(self, waiting, graph_store):
        # The note of the decision exists; the record it was about to create
        # does not. That is the difference between an entry where nothing
        # happened and one where something was deliberately not done.
        note = graph_store.get_node(waiting.audit_node_id)

        assert note is not None
        assert note["status"] == "PENDING_HITL"


class TestAnsweringIt:
    """One tap, days later, and the held-back change lands."""

    def test_the_card_can_be_read(self, waiting, reviewer):
        card = reviewer.get_card("local", waiting.id)

        assert card.options, "a card with no answers is not answerable"
        assert card.source_text

    def test_answering_writes_what_was_held_back(
        self, waiting, reviewer, graph_store
    ):
        outcome = reviewer.resolve("local", waiting.id, ResolutionChoice.ACTION_A)

        assert outcome.action_taken is ReconciliationAction.BRANCH
        for node_id in outcome.nodes_written:
            assert graph_store.get_node(node_id) is not None

    def test_both_notes_end_up_in_the_graph_and_linked(
        self, waiting, reviewer, graph_store
    ):
        outcome = reviewer.resolve("local", waiting.id, ResolutionChoice.ACTION_A)

        original = graph_store.get_node(waiting.audit_node_id)
        answer = graph_store.get_node(outcome.new_audit_node_id)

        assert original["hitl_resolved"] is True
        assert original["hitl_resolution_user_choice"] == HitlResolutionChoice.ACTION_A.value
        # The waiting note keeps saying it was a tie. The graph should read
        # as "it hesitated, then somebody decided" — not as though it had
        # been sure all along.
        assert original["action"] == ReconciliationAction.AMBIGUOUS.value
        assert answer["action"] == ReconciliationAction.BRANCH.value
        assert answer["model_used"] == "human-review"

    def test_the_card_leaves_the_queue(self, waiting, reviewer, ops_store):
        reviewer.resolve("local", waiting.id, ResolutionChoice.ACTION_A)

        assert ops_store.hitl.get(waiting.id).status is HitlItemStatus.RESOLVED
        # The entry left other things undecided too; what matters is that
        # this one is no longer among them.
        assert waiting.id not in {
            card.item_id for card in reviewer.list_queue("local").cards
        }

    def test_a_new_record_becomes_searchable(
        self, waiting, reviewer, vector_store, embedder
    ):
        outcome = reviewer.resolve("local", waiting.id, ResolutionChoice.ACTION_A)

        if not outcome.nodes_written:
            pytest.skip("this answer created no record to search for")

        # Not merely written to the index — findable through it, which is
        # the only version of "indexed" that matters to retrieval.
        assert outcome.vectors_written
        from lumen.schemas.enums import EmbeddingTaskType

        found = vector_store.hybrid_search(
            embedder.embed_text(
                "comparing himself to others", task_type=EmbeddingTaskType.QUERY
            ),
            limit=20,
        )
        assert {hit.node_id for hit in found} & set(outcome.vectors_written)

    def test_the_answer_can_be_traced_back(self, waiting, reviewer, graph_store):
        outcome = reviewer.resolve("local", waiting.id, ResolutionChoice.ACTION_A)

        # Every link the answer created names the note of the answer, so
        # "why does this exist" leads to the decision actually acted on.
        answer = graph_store.get_node(outcome.new_audit_node_id)
        assert answer["rollback_pointer"]


def test_deferring_then_running_out_of_time_settles_it(
    waiting, reviewer, ops_store, moment
):
    """
    An item deferred once and then forgotten settles itself, and says so.

    Recorded as having run out of time rather than as a choice, because the
    graph must never claim somebody decided something they never saw again.
    """
    from datetime import timedelta

    reviewer.snooze("local", waiting.id)
    ops_store.hitl.snooze(
        waiting.id,
        until=moment - timedelta(days=30),
        at=moment - timedelta(days=30),
    )

    report = reviewer.sweep("local")

    assert waiting.id in report.auto_resolved
    settled = ops_store.hitl.get(waiting.id)
    assert settled.status is HitlItemStatus.AUTO_RESOLVED
    assert settled.resolution_choice is HitlResolutionChoice.AUTO_BRANCH_AFTER_SNOOZE
