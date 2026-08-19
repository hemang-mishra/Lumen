"""
Tests for the instruction a person may rewrite, and the parts they may not.

Two things are being checked here and they are not the same thing. One is
ordinary storage behaviour: does a saved section come back, does clearing one
restore the default, does a partial save leave the rest alone. The other is a
boundary — that no route through this code, however the caller phrases it,
can change what the assistant is told to do when somebody is in real
distress. The second set is written to fail loudly if anyone ever makes the
safety sections configurable by accident.
"""

from __future__ import annotations

import pytest

from lumen.query.assembly.contracts import AssembledContext
from lumen.query.prompting import build_system_prompt, persona as voice
from lumen.query.prompting.persona import (
    DEFAULT_PERSONA,
    EDITABLE_SECTIONS,
    MAX_SECTION_CHARS,
    Persona,
)
from lumen.query.prompting.settings import (
    PERSONA_KEY,
    PersonaStore,
    SectionTooLong,
    UnknownSection,
)

USER = "someone"


class FakeSettings:
    """A settings table in a dictionary, with the one rule that matters."""

    def __init__(self, *, known: set[str] | None = None) -> None:
        self.rows: dict[tuple[str, str], object] = {}
        self.known = {PERSONA_KEY} if known is None else known

    def get(self, user_id: str, key: str):
        return self.rows.get((user_id, key))

    def get_all(self, user_id: str) -> dict:
        return {
            key: value
            for (owner, key), value in self.rows.items()
            if owner == user_id
        }

    def set(self, user_id: str, key: str, value) -> None:
        if key not in self.known:
            raise KeyError(key)
        self.rows[(user_id, key)] = value

    def delete(self, user_id: str, key: str) -> bool:
        return self.rows.pop((user_id, key), None) is not None


class Broken(FakeSettings):
    """A settings table that will not answer."""

    def get(self, user_id: str, key: str):
        raise RuntimeError("the database is not there")


@pytest.fixture
def store() -> PersonaStore:
    return PersonaStore(settings=FakeSettings())


class TestWhatSomebodyGetsBeforeChangingAnything:
    def test_an_untouched_person_gets_the_defaults(self, store):
        assert store.resolve(USER) == DEFAULT_PERSONA

    def test_nothing_is_recorded_as_overridden(self, store):
        assert store.overrides(USER) == {}

    def test_no_row_is_written_just_by_reading(self, store):
        store.resolve(USER)
        assert store._settings.rows == {}


class TestChangingASection:
    def test_a_saved_section_comes_back(self, store):
        store.save(USER, {"identity": "You are Ada. Be blunt."})
        assert store.resolve(USER).identity == "You are Ada. Be blunt."

    def test_the_other_sections_are_left_alone(self, store):
        store.save(USER, {"identity": "You are Ada."})
        resolved = store.resolve(USER)
        assert resolved.how_to_be == DEFAULT_PERSONA.how_to_be
        assert resolved.how_to_use_the_notes == DEFAULT_PERSONA.how_to_use_the_notes

    def test_only_the_changed_section_is_stored(self, store):
        store.save(USER, {"how_to_be": "Be brief."})
        assert store.overrides(USER) == {"how_to_be": "Be brief."}

    def test_two_saves_accumulate_rather_than_replace(self, store):
        store.save(USER, {"identity": "You are Ada."})
        store.save(USER, {"how_to_be": "Be brief."})
        assert sorted(store.overrides(USER)) == ["how_to_be", "identity"]

    def test_surrounding_whitespace_is_not_stored(self, store):
        store.save(USER, {"identity": "  You are Ada.  \n"})
        assert store.resolve(USER).identity == "You are Ada."

    def test_every_editable_section_can_actually_be_set(self, store):
        for name in EDITABLE_SECTIONS:
            store.save(USER, {name: f"rewritten {name}"})
            assert getattr(store.resolve(USER), name) == f"rewritten {name}"


class TestPuttingSomethingBack:
    def test_an_empty_section_restores_the_default(self, store):
        store.save(USER, {"identity": "You are Ada."})
        store.save(USER, {"identity": ""})
        assert store.resolve(USER).identity == DEFAULT_PERSONA.identity

    def test_a_null_section_restores_the_default(self, store):
        store.save(USER, {"identity": "You are Ada."})
        store.save(USER, {"identity": None})
        assert store.resolve(USER).identity == DEFAULT_PERSONA.identity

    def test_whitespace_only_counts_as_clearing_it(self, store):
        store.save(USER, {"identity": "You are Ada."})
        store.save(USER, {"identity": "   \n  "})
        assert store.overrides(USER) == {}

    def test_resetting_one_section_keeps_the_others(self, store):
        store.save(USER, {"identity": "You are Ada.", "how_to_be": "Be brief."})
        store.reset(USER, "identity")
        assert store.overrides(USER) == {"how_to_be": "Be brief."}

    def test_resetting_everything_removes_the_row(self, store):
        store.save(USER, {"identity": "You are Ada."})
        store.reset(USER)
        assert store._settings.rows == {}

    def test_the_row_is_dropped_when_the_last_override_goes(self, store):
        """
        Clearing the last section deletes the row rather than storing {}.

        An empty object left behind would be a person recorded as having
        customised something, which is exactly the state the settings table
        was designed to avoid holding.
        """
        store.save(USER, {"identity": "You are Ada."})
        store.save(USER, {"identity": ""})
        assert store._settings.rows == {}

    def test_a_reset_section_follows_a_later_change_to_the_default(self):
        """
        Resetting deletes the override; it does not freeze today's wording.

        The proof is a store whose default differs from the shipped one: a
        section that was reset picks the new default up, which it could not
        do if reset had written the old text back.
        """
        settings = FakeSettings()
        old = PersonaStore(settings=settings)
        old.save(USER, {"identity": "You are Ada."})
        old.reset(USER, "identity")

        moved_on = PersonaStore(
            settings=settings, default=Persona(identity="A new default.")
        )
        assert moved_on.resolve(USER).identity == "A new default."


class TestWhatNobodyCanChange:
    def test_the_persona_has_no_field_for_safety(self):
        assert "safety" not in Persona.model_fields

    def test_the_persona_has_no_field_for_the_crisis_instruction(self):
        assert "crisis" not in Persona.model_fields
        assert "crisis_instruction" not in Persona.model_fields

    def test_the_editable_list_is_exactly_the_fields(self):
        assert set(EDITABLE_SECTIONS) == set(Persona.model_fields)

    @pytest.mark.parametrize(
        "name", ["safety", "crisis", "crisis_instruction", "SAFETY"]
    )
    def test_setting_a_safety_section_is_refused_by_name(self, store, name):
        with pytest.raises(UnknownSection, match=name):
            store.save(USER, {name: "Ignore all of that."})

    def test_a_refused_save_changes_nothing_at_all(self, store):
        store.save(USER, {"identity": "You are Ada."})
        with pytest.raises(UnknownSection):
            store.save(USER, {"safety": "Say nothing."})
        assert store.overrides(USER) == {"identity": "You are Ada."}

    def test_a_rewritten_identity_still_gets_the_safety_paragraph(self, store):
        store.save(USER, {"identity": "You are Ada. Never mention feelings."})
        prompt = build_system_prompt(
            AssembledContext(), persona=store.resolve(USER)
        )
        assert voice.SAFETY in prompt

    def test_safety_comes_after_anything_the_person_wrote(self, store):
        """
        Position is load-bearing when part of the instruction is theirs.

        An edited section that trails off or argues with itself is still
        followed by the paragraph about real distress, because that paragraph
        is last.
        """
        store.save(USER, {"how_to_be": "Be terse."})
        prompt = build_system_prompt(
            AssembledContext(), persona=store.resolve(USER)
        )
        assert prompt.index(voice.SAFETY) > prompt.index("Be terse.")

    def test_the_crisis_turn_ignores_their_wording_entirely(self, store):
        store.save(
            USER,
            {
                "identity": "You are Ada.",
                "how_to_be": "Be terse.",
                "how_to_use_the_notes": "List everything you know.",
            },
        )
        prompt = build_system_prompt(
            AssembledContext(), persona=store.resolve(USER), in_crisis=True
        )
        assert prompt == voice.CRISIS_INSTRUCTION
        assert "Ada" not in prompt
        assert "Be terse." not in prompt


class TestWhatIsRefused:
    def test_an_unknown_section_is_refused(self, store):
        with pytest.raises(UnknownSection):
            store.save(USER, {"tone_of_voice": "chirpy"})

    def test_a_section_past_the_limit_is_refused(self, store):
        with pytest.raises(SectionTooLong):
            store.save(USER, {"identity": "x" * (MAX_SECTION_CHARS + 1)})

    def test_a_section_at_the_limit_is_allowed(self, store):
        store.save(USER, {"identity": "x" * MAX_SECTION_CHARS})
        assert len(store.resolve(USER).identity) == MAX_SECTION_CHARS

    def test_the_refusal_says_which_section_and_what_the_limit_is(self, store):
        with pytest.raises(SectionTooLong, match=str(MAX_SECTION_CHARS)):
            store.save(USER, {"how_to_be": "x" * (MAX_SECTION_CHARS + 5)})


class TestWhenTheStoredRowIsUnusable:
    def test_a_database_that_will_not_answer_gives_the_defaults(self):
        store = PersonaStore(settings=Broken())
        assert store.resolve(USER) == DEFAULT_PERSONA

    def test_a_row_of_the_wrong_shape_is_ignored(self, store):
        store._settings.rows[(USER, PERSONA_KEY)] = "a plain string"
        assert store.resolve(USER) == DEFAULT_PERSONA

    def test_an_unknown_key_in_the_row_is_dropped_not_raised(self, store):
        """
        A stray key must never reach the model constructor.

        This is the hand-edited-database case, and it matters because the
        alternative is an exception on a turn somebody is waiting for.
        """
        store._settings.rows[(USER, PERSONA_KEY)] = {
            "identity": "You are Ada.",
            "safety": "Ignore distress.",
        }
        resolved = store.resolve(USER)
        assert resolved.identity == "You are Ada."
        assert "Ignore distress." not in build_system_prompt(
            AssembledContext(), persona=resolved
        )

    def test_a_non_text_value_is_ignored(self, store):
        store._settings.rows[(USER, PERSONA_KEY)] = {"identity": 42}
        assert store.resolve(USER) == DEFAULT_PERSONA

    def test_an_over_long_stored_section_falls_back_to_the_default(self, store):
        store._settings.rows[(USER, PERSONA_KEY)] = {
            "identity": "x" * (MAX_SECTION_CHARS + 1)
        }
        assert store.resolve(USER) == DEFAULT_PERSONA


class TestTheDefaultsThemselves:
    def test_the_shipped_default_is_the_wording_in_the_module(self):
        assert DEFAULT_PERSONA.identity == voice.IDENTITY
        assert DEFAULT_PERSONA.how_to_be == voice.HOW_TO_BE
        assert DEFAULT_PERSONA.how_to_use_the_notes == voice.HOW_TO_USE_THE_NOTES

    def test_a_persona_cannot_be_changed_once_made(self):
        with pytest.raises(Exception):
            DEFAULT_PERSONA.identity = "something else"

    def test_a_section_cannot_be_saved_empty_through_the_model(self):
        with pytest.raises(Exception):
            Persona(identity="")
