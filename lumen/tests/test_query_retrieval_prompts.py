"""
What the model is told when writing the text a turn is searched with.

Prompt text changes without the code changing, so these tests check the two
things that are structural rather than editorial: that every reason reaches
the instruction with what narrowed it, and that the fallback text still
carries enough to search with when no model answers.
"""

from __future__ import annotations

from lumen.query.retrieval import prompts
from lumen.schemas.enums import Domain, TriggerType
from lumen.schemas.query import RetrievalTrigger


def trigger(**fields) -> RetrievalTrigger:
    return RetrievalTrigger(
        trigger_type=fields.pop("trigger_type", TriggerType.PATTERN_MENTION), **fields
    )


class TestTheNumberedList:
    def test_each_reason_gets_its_own_number(self):
        rendered = prompts.render_items(
            [trigger(), trigger(trigger_type=TriggerType.SOMATIC_MARKER)]
        )

        assert rendered.startswith("1. ")
        assert "\n2. " in rendered

    def test_what_narrowed_a_reason_travels_with_it(self):
        # Without this, two reasons from one sentence produce the same
        # search twice.
        rendered = prompts.render_items(
            [trigger(domain=Domain.CAREER, era="hostel", keywords=("stuck",))]
        )

        assert "career" in rendered
        assert "hostel" in rendered
        assert "stuck" in rendered

    def test_the_names_are_written_as_words(self):
        # An instruction that reads like code invites an answer that does.
        rendered = prompts.render_items([trigger(trigger_type=TriggerType.SOMATIC_MARKER)])

        assert "somatic marker" in rendered
        assert "SOMATIC_MARKER" not in rendered

    def test_nothing_to_ask_about_renders_nothing(self):
        assert prompts.render_items([]) == ""


class TestTheWholeInstruction:
    def test_the_sentence_is_in_it(self):
        built = prompts.build_prompt("I keep avoiding it", [trigger()])

        assert "I keep avoiding it" in built

    def test_the_reasons_are_in_it(self):
        built = prompts.build_prompt("anything", [trigger(keywords=("resistance",))])

        assert "resistance" in built

    def test_it_asks_for_journal_writing_rather_than_a_reply(self):
        # The whole trick: what is being searched for was written down
        # afterwards as settled reflection, not said out loud mid-thought.
        built = prompts.build_prompt("anything", [trigger()])

        assert "journal entry" in built
        assert "no questions and no" in built


class TestTheFallbackText:
    def test_it_keeps_the_sentence_and_what_narrowed_the_reason(self):
        text = prompts.own_words(
            "I keep avoiding it", trigger(keywords=("resistance",), era="hostel")
        )

        assert "I keep avoiding it" in text
        assert "resistance" in text
        assert "hostel" in text

    def test_a_reason_that_narrowed_nothing_leaves_the_sentence_alone(self):
        assert prompts.own_words("  I keep avoiding it  ", trigger(keywords=())) == (
            "I keep avoiding it"
        )

    def test_blank_keywords_are_ignored(self):
        assert prompts.own_words("said", trigger(keywords=("  ",))) == "said"
