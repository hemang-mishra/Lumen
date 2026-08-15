"""
Tests for what may be asked about again, and how the asking is worded.

Both halves of this file guard the same thing. Asking a model to try again
is asking it for output, and it will produce some — so the set of problems
worth re-asking has to stay small and fixed, and the wording of the request
has to leave "I cannot" available as an answer.

The membership test below is deliberately an exact-equality assertion
rather than a set of individual checks. Adding a rule to the retryable set
should be a change somebody has to make on purpose, with this test in front
of them.
"""

from __future__ import annotations

import pytest

from lumen.pipeline.extraction.contracts import (
    DropRule,
    ExtractedCausalChain,
    ExtractedCausalStep,
    ExtractedEvent,
    ExtractedObservation,
    RejectedItem,
)
from lumen.pipeline.extraction.prompts import (
    CORRECTION_PROMPT,
    needs_type_dictionary,
    render_correction_items,
)
from lumen.pipeline.extraction.retry import RETRYABLE_RULES


def rejection(rule: DropRule, *, detail: str = "", payload=None, kind="observation"):
    return RejectedItem(
        item_kind=kind,
        index=0,
        rule=rule,
        detail=detail,
        payload=payload or ExtractedObservation(type="VIBES", content="felt off"),
    )


class TestWhatMayBeAskedAgain:
    def test_the_retryable_set_is_exactly_these_five(self):
        # An exact match rather than five separate checks, so widening the
        # set is a deliberate act with this test in the way.
        assert RETRYABLE_RULES == {
            DropRule.UNKNOWN_TYPE,
            DropRule.UNKNOWN_ENUM_VALUE,
            DropRule.SIGNAL_FLOOR,
            DropRule.UNKNOWN_STEP_TYPE,
            DropRule.EMPTY_CONTENT,
        }

    def test_a_missing_quote_is_never_asked_about_again(self):
        # The most important line in this file. This rule fires when a thin
        # entry produced a feeling nobody stated; a correction asking for
        # the missing quote is an instruction to write one, and the written
        # one would pass the check it was meant to fail.
        assert DropRule.QUOTE_NOT_FOUND not in RETRYABLE_RULES

    @pytest.mark.parametrize(
        "rule",
        [
            DropRule.EXCLUDED_TYPE,
            DropRule.TYPE_NOT_ALLOWED_HERE,
            DropRule.CHAIN_TOO_SHORT,
            DropRule.OVER_LIMIT,
            DropRule.QUOTE_NOT_FOUND,
            DropRule.UNKNOWN_PERSON,
            DropRule.NOT_CORRECTED,
        ],
        ids=lambda rule: rule.value,
    )
    def test_nothing_unfixable_is_asked_about_again(self, rule):
        assert rule not in RETRYABLE_RULES

    def test_every_rule_is_classified_one_way_or_the_other(self):
        # A rule added to the drop reasons without a decision about whether
        # it is retryable would silently land in the terminal half.
        assert RETRYABLE_RULES <= set(DropRule)


class TestHowTheRequestIsWorded:
    def test_it_names_the_field_at_fault(self):
        written = render_correction_items((rejection(DropRule.UNKNOWN_TYPE, detail="VIBES"),))

        assert "VIBES" in written
        assert "not one of the available types" in written

    def test_it_never_asks_for_evidence(self):
        # Nothing in the retryable set is about evidence, and a request for
        # quotes is a request to produce them.
        #
        # Only what instructs the model is checked: the template and the
        # explanation of each problem. The item echoed underneath is the
        # model's own words being shown back to it, and the field names in
        # it are not asking for anything.
        instructions = CORRECTION_PROMPT + "\n".join(
            line
            for rule in RETRYABLE_RULES
            for line in render_correction_items((rejection(rule),)).splitlines()
            if "Problem:" in line
        )

        for banned in ("quote", "evidence"):
            assert banned not in instructions.lower()

    def test_leaving_an_item_out_is_offered_as_an_answer(self):
        # Without this, every correction is a demand for output, which is
        # how a correction becomes an invention.
        assert "leave it out" in CORRECTION_PROMPT.lower()

    def test_it_says_not_to_touch_anything_else(self):
        assert "do not add anything new" in CORRECTION_PROMPT.lower()

    def test_the_item_is_shown_back_as_it_was_returned(self):
        written = render_correction_items(
            (
                rejection(
                    DropRule.UNKNOWN_TYPE,
                    detail="VIBES",
                    payload=ExtractedObservation(type="VIBES", content="the comparing hurts"),
                ),
            )
        )

        assert "the comparing hurts" in written

    @pytest.mark.parametrize(
        "rule, expected",
        [
            (DropRule.UNKNOWN_ENUM_VALUE, "not a value that exists"),
            (DropRule.SIGNAL_FLOOR, "HIGH or CRITICAL"),
            (DropRule.EMPTY_CONTENT, "nothing written in it"),
            (DropRule.UNKNOWN_STEP_TYPE, "TRIGGER"),
        ],
        ids=lambda value: str(value)[:20],
    )
    def test_each_problem_is_explained_in_its_own_terms(self, rule, expected):
        assert expected in render_correction_items((rejection(rule),))

    def test_an_unexpected_problem_still_produces_a_sentence(self):
        written = render_correction_items((rejection(DropRule.OVER_LIMIT),))

        assert "did not pass the rules" in written

    def test_items_are_grouped_under_the_headings_they_came_back_in(self):
        written = render_correction_items(
            (
                rejection(DropRule.UNKNOWN_TYPE),
                rejection(
                    DropRule.EMPTY_CONTENT,
                    kind="event",
                    payload=ExtractedEvent(event_summary="went out"),
                ),
                rejection(
                    DropRule.UNKNOWN_STEP_TYPE,
                    kind="chain",
                    payload=ExtractedCausalChain(
                        chain_summary="a to b",
                        causal_chain=[ExtractedCausalStep(step=1, type="X", content="c")],
                    ),
                ),
            )
        )

        assert "FINDINGS THAT NEED CORRECTING" in written
        assert "EVENTS THAT NEED CORRECTING" in written
        assert "SEQUENCES THAT NEED CORRECTING" in written

    def test_a_heading_with_nothing_under_it_is_left_out(self):
        written = render_correction_items((rejection(DropRule.UNKNOWN_TYPE),))

        assert "EVENTS THAT NEED CORRECTING" not in written

    def test_items_are_numbered_from_one_within_their_group(self):
        written = render_correction_items(
            (rejection(DropRule.UNKNOWN_TYPE), rejection(DropRule.EMPTY_CONTENT))
        )

        assert "1. Problem:" in written
        assert "2. Problem:" in written


class TestWhenTheTypeListIsRepeated:
    @pytest.mark.parametrize("rule", [DropRule.UNKNOWN_TYPE, DropRule.SIGNAL_FLOOR])
    def test_it_comes_back_when_a_type_was_the_problem(self, rule):
        assert needs_type_dictionary((rejection(rule),)) is True

    @pytest.mark.parametrize(
        "rule", [DropRule.EMPTY_CONTENT, DropRule.UNKNOWN_ENUM_VALUE, DropRule.UNKNOWN_STEP_TYPE]
    )
    def test_it_stays_out_when_the_problem_was_something_else(self, rule):
        # It is a large block of text. Repeating it for an unrelated mistake
        # spends most of the prompt saying nothing new.
        assert needs_type_dictionary((rejection(rule),)) is False

    def test_one_type_problem_among_others_brings_it_back(self):
        rejections = (rejection(DropRule.EMPTY_CONTENT), rejection(DropRule.UNKNOWN_TYPE))

        assert needs_type_dictionary(rejections) is True
