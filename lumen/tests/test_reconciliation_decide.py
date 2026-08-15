"""
Tests for asking the models how today relates to the past.

Three things are worth protecting here.

The whole entry costs one call, and only the answers that permanently alter
something long-held cost a second. An entry with twenty findings that asked
twenty times would make this the most expensive step in the pipeline by a
wide margin, for a question that is mostly "is this the same as that?".

Answers are matched to findings by number. A reply that loses one item must
not shift every later decision onto the wrong finding — that failure does
not look like a failure, it looks like a confident decision about something
else entirely.

And the careful model can only ever make an answer safer. It is shown the
risky readings and nothing else, so there is nothing for it to escalate.
"""

from __future__ import annotations

import json

import pytest

from lumen.pipeline.reconciliation import decide
from lumen.pipeline.reconciliation.contracts import (
    DecisionResponse,
    ItemDecision,
    ProposedAction,
)
from lumen.providers.errors import ProviderError
from lumen.providers.fake import FakeLLMProvider
from lumen.schemas.enums import ModelRole


def reply(*decisions, people=None) -> str:
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
                }
                for index, action, target, confidence in decisions
            ],
            "people": people or [],
        }
    )


def proposal(index: int, action: str, *, target: str | None = "pat_old") -> ItemDecision:
    return ItemDecision(
        item_index=index,
        primary=ProposedAction(
            action=action, target_node_id=target, confidence=0.95, reason="because"
        ),
    )


@pytest.fixture
def light():
    def _build(replies):
        return FakeLLMProvider(
            list(replies), role=ModelRole.LIGHTWEIGHT, model="fake-light"
        )

    return _build


class TestOneCallForTheWholeEntry:
    def test_every_finding_is_asked_about_at_once(self, make_item, light):
        provider = light([reply((1, "MERGE", "pat_a", 0.9), (2, "BRANCH", None, 0.8))])
        items = [make_item(node_id="obs_1"), make_item(node_id="obs_2")]

        response = decide.propose(items, provider=provider)

        assert len(provider.calls) == 1
        assert len(response.decisions) == 2

    def test_nothing_to_decide_costs_no_call(self, light):
        provider = light([])

        response = decide.propose([], provider=provider)

        assert response == DecisionResponse()
        assert provider.calls == []

    def test_the_prompt_shows_each_candidate_and_how_it_was_found(
        self, make_item, make_candidate, light
    ):
        provider = light([reply((1, "MERGE", "pat_a", 0.9))])
        item = make_item(
            candidates=[
                make_candidate("pat_a", score=0.83),
                make_candidate("bel_b", node_type="BeliefNode", anchor="NAMED_PERSON"),
            ]
        )

        decide.propose([item], provider=provider)

        prompt = provider.calls[0].prompt
        assert "id=pat_a" in prompt
        assert "closeness 0.83" in prompt
        assert "FOUND BY ANCHOR (NAMED_PERSON)" in prompt

    def test_the_prompt_says_recording_separately_is_the_safe_answer(
        self, make_item, light
    ):
        # A model asked "has this person changed?" will find change, because
        # that is the more interesting answer. No amount of checking
        # afterwards recovers the careful reading; only the wording does.
        provider = light([reply((1, "BRANCH", None, 0.8))])

        decide.propose([make_item()], provider=provider)

        prompt = provider.calls[0].prompt
        assert "BRANCH is the safe answer" in prompt


class TestMatchingAnswersToFindings:
    def test_answers_are_matched_by_number(self, make_item):
        response = DecisionResponse(
            decisions=[proposal(2, "MERGE"), proposal(1, "BRANCH")]
        )

        matched = decide.align(response, item_count=2)

        assert matched[1].primary.action == "BRANCH"
        assert matched[2].primary.action == "MERGE"

    def test_a_missing_answer_stays_missing(self):
        # The alternative is shifting the next answer up, which attaches a
        # confident decision to a finding nobody made it about.
        response = DecisionResponse(decisions=[proposal(1, "MERGE"), proposal(3, "EVOLVE")])

        matched = decide.align(response, item_count=3)

        assert set(matched) == {1, 3}
        assert 2 not in matched

    def test_an_answer_for_a_finding_nobody_asked_about_is_dropped(self):
        response = DecisionResponse(decisions=[proposal(1, "MERGE"), proposal(9, "EVOLVE")])

        matched = decide.align(response, item_count=1)

        assert set(matched) == {1}

    def test_a_repeated_number_keeps_the_first_answer(self):
        response = DecisionResponse(
            decisions=[proposal(1, "MERGE"), proposal(1, "EVOLVE")]
        )

        matched = decide.align(response, item_count=1)

        assert matched[1].primary.action == "MERGE"


class TestTheSecondOpinion:
    @pytest.mark.parametrize("action", ["EVOLVE", "CONTRADICT", "DIALECTIC"])
    def test_the_three_heavy_readings_are_checked(self, action):
        assert decide.needs_confirming(proposal(1, action)) is True

    @pytest.mark.parametrize("action", ["MERGE", "REINFORCE", "BRANCH", "REGULATE"])
    def test_the_safer_readings_are_not(self, action):
        assert decide.needs_confirming(proposal(1, action)) is False

    def test_an_unrecognised_action_is_not_sent_for_confirming(self):
        # Nothing to confirm. It is refused later, by name.
        assert decide.needs_confirming(proposal(1, "MERGE_ISH")) is False

    def test_the_verdict_comes_back_keyed_by_finding(self, make_item, light):
        provider = light(
            [
                json.dumps(
                    {
                        "verdicts": [
                            {
                                "item_index": 1,
                                "confirmed": True,
                                "primary": {"action": "EVOLVE", "confidence": 0.95},
                                "delta_description": "he stopped needing it",
                            }
                        ]
                    }
                )
            ]
        )

        verdicts = decide.confirm(
            [make_item()], [proposal(1, "EVOLVE")], provider=provider
        )

        assert verdicts[1].confirmed is True
        assert verdicts[1].delta_description == "he stopped needing it"

    def test_the_careful_model_only_sees_the_risky_items(
        self, make_item, make_candidate, light
    ):
        provider = light([json.dumps({"verdicts": []})])
        items = [
            make_item(node_id="obs_1", candidates=[make_candidate("pat_a")]),
            make_item(node_id="obs_2", candidates=[make_candidate("pat_b")]),
        ]

        decide.confirm(
            items, [proposal(2, "EVOLVE", target="pat_b")], provider=provider
        )

        prompt = provider.calls[0].prompt
        assert "pat_b" in prompt
        assert "pat_a" not in prompt

    def test_nothing_risky_means_no_second_call(self, make_item, light):
        provider = light([])

        assert decide.confirm([make_item()], [], provider=provider) == {}
        assert provider.calls == []


class TestWhenTheAnswerCannotBeRead:
    def test_a_failed_call_is_repeated(self, make_item):
        def always_fails(_prompt):
            raise ProviderError("network gone")

        provider = FakeLLMProvider(
            always_fails, role=ModelRole.LIGHTWEIGHT, model="fake-light"
        )

        assert decide.propose([make_item()], provider=provider, attempts=2) is None
        assert len(provider.calls) == 2

    def test_an_unreadable_reply_is_repeated_then_given_up_on(self, make_item, light):
        provider = light(["not json at all", "still not json"])

        assert decide.propose([make_item()], provider=provider, attempts=2) is None
        assert len(provider.calls) == 2

    def test_a_second_attempt_that_works_is_used(self, make_item, light):
        provider = light(["not json at all", reply((1, "MERGE", "pat_a", 0.9))])

        response = decide.propose([make_item()], provider=provider, attempts=2)

        assert response is not None
        assert response.decisions[0].primary.action == "MERGE"

    def test_a_failed_second_opinion_leaves_the_first_reading_alone(
        self, make_item, light
    ):
        # Not a fallback to acting: the bar for these three actions is high
        # enough that the first reading is usually held back anyway.
        provider = light(["unreadable", "unreadable"])

        assert (
            decide.confirm(
                [make_item()], [proposal(1, "EVOLVE")], provider=provider, attempts=2
            )
            == {}
        )

    def test_no_journal_text_reaches_the_log(self, make_item, light, captured_logs):
        provider = light(["not json"])
        secret = "the thing I have never told anyone"

        decide.propose([make_item(secret)], provider=provider, attempts=1)

        assert not any(secret in json.dumps(line) for line in captured_logs)


class TestARepliedShapeNobodyAskedFor:
    def test_a_reply_of_the_wrong_shape_is_treated_as_unreadable(
        self, make_item, light
    ):
        # Valid JSON, wrong contents. There is nothing to correct here, so
        # it is asked for again and then given up on honestly.
        provider = light([json.dumps({"decisions": "all of them"})])

        assert decide.propose([make_item()], provider=provider, attempts=1) is None
