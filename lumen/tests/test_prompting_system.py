"""
What the assistant is actually told.

Prompt wording changes without code changing, so these tests deliberately do
not pin sentences. What they pin is structure and the two behaviours that
would be real failures if they broke: a briefing appearing without the
instruction for handling it, and the ordinary instructions surviving into a
moment where somebody is in acute distress.
"""

from __future__ import annotations

import pytest

from lumen.query.assembly import block
from lumen.query.assembly.contracts import AssembledContext, ContextItem
from lumen.query.prompting import build_system_prompt, persona
from lumen.query.prompting.system import SUMMARY_HEADING
from lumen.schemas.enums import EmotionalRegister, RetrievalPass


def item(text: str = "Pattern: avoiding going places alone. Seen 4 times.") -> ContextItem:
    return ContextItem(
        node_id="pat_1",
        node_type="PatternNode",
        text=text,
        tokens=12,
        found_by=RetrievalPass.SEMANTIC,
    )


def context(*items: ContextItem, **fields) -> AssembledContext:
    return AssembledContext(items=tuple(items), **fields)


class TestTheOrdinaryInstructions:
    def test_they_say_who_the_assistant_is(self):
        assert "You are Lumen" in build_system_prompt(context())

    def test_they_say_how_to_be(self):
        prompt = build_system_prompt(context())

        assert "How to be with them" in prompt

    def test_they_are_not_a_wall_of_policy(self):
        # A long instruction is not followed more carefully than a short one;
        # it is followed more selectively, and nobody can predict which parts
        # survive.
        prompt = build_system_prompt(context(item()))

        assert len(prompt.split()) < 500

    def test_they_never_claim_to_be_a_therapist(self):
        # Pretending otherwise with somebody in real difficulty is the
        # failure that matters most here.
        prompt = build_system_prompt(context()).lower()

        assert "you are not their therapist" in prompt

    def test_safety_is_always_present(self):
        assert "distress" in build_system_prompt(context())


class TestTheBriefing:
    def test_it_appears_when_there_is_one(self):
        prompt = build_system_prompt(context(item()))

        assert "avoiding going places alone" in prompt
        assert block.OPENING in prompt

    def test_it_comes_with_the_instruction_for_handling_it(self):
        prompt = build_system_prompt(context(item()))

        assert "About your notes on them" in prompt

    def test_neither_appears_when_there_is_nothing_to_say(self):
        # A heading with nothing under it is not neutral: "what you know
        # about them" followed by silence reads as a claim that there is
        # nothing to know.
        prompt = build_system_prompt(context())

        assert block.OPENING not in prompt
        assert "About your notes on them" not in prompt

    def test_the_assistant_is_told_to_absorb_it_rather_than_recite_it(self):
        prompt = build_system_prompt(context(item()))

        assert "Do not read it out" in prompt

    def test_a_briefing_carried_from_the_previous_turn_says_so(self):
        prompt = build_system_prompt(context(item(), deferred=True))

        assert block.DEFERRED_NOTE in prompt

    def test_every_line_of_the_briefing_reaches_the_prompt(self):
        prompt = build_system_prompt(
            context(item("first thing noticed"), item("second thing noticed"))
        )

        assert "first thing noticed" in prompt
        assert "second thing noticed" in prompt


class TestTheConversationSoFar:
    def test_it_appears_when_there_is_one(self):
        prompt = build_system_prompt(context(), summary="They came in about work.")

        assert SUMMARY_HEADING in prompt
        assert "They came in about work." in prompt

    def test_and_not_when_there_is_not(self):
        assert SUMMARY_HEADING not in build_system_prompt(context(), summary=None)

    def test_an_empty_summary_counts_as_none(self):
        assert SUMMARY_HEADING not in build_system_prompt(context(), summary="   ")


class TestWhenSomebodyIsInCrisis:
    def test_the_instructions_change_entirely(self):
        # Withholding the notes while still asking for curiosity and
        # pattern-noticing would be half a decision.
        prompt = build_system_prompt(context(item()), in_crisis=True)

        assert prompt == persona.CRISIS_INSTRUCTION

    def test_no_briefing_survives_into_them(self):
        prompt = build_system_prompt(context(item()), in_crisis=True)

        assert "avoiding going places alone" not in prompt
        assert block.OPENING not in prompt

    def test_no_summary_survives_either(self):
        prompt = build_system_prompt(
            context(), summary="They came in about work.", in_crisis=True
        )

        assert "They came in about work." not in prompt

    def test_they_say_what_to_do_instead(self):
        prompt = build_system_prompt(context(), in_crisis=True)

        assert "Be present" in prompt
        assert "Do not analyse" in prompt

    def test_they_still_point_somewhere_real(self):
        assert "emergency services" in build_system_prompt(context(), in_crisis=True)


class TestTheBlockItself:
    def test_an_empty_briefing_renders_as_nothing(self):
        assert block.render(context()) == ""

    def test_each_line_is_its_own_bullet(self):
        rendered = block.render(context(item("first"), item("second")))

        assert "- first" in rendered
        assert "- second" in rendered

    def test_it_is_closed_as_well_as_opened(self):
        rendered = block.render(context(item()))

        assert rendered.startswith(block.OPENING)
        assert rendered.endswith(block.CLOSING)

    def test_it_says_the_notes_are_not_from_this_conversation(self):
        # Without that, the assistant can quote a pattern back as though it
        # had been mentioned this turn.
        assert "not from this chat" in block.OPENING
