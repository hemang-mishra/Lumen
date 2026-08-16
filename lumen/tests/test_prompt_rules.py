"""
Tests for the rules every prompt in the system carries.

The point of these is coverage rather than wording: a rule that holds
everywhere is only worth stating once, and the way it fails is that somebody
adds a sixth stage and writes its instructions from scratch. So the check
walks the package and finds the prompt modules itself rather than listing
them, because a list is the thing that would go stale.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest

import lumen
from lumen.prompt_rules import AUTHOR_NAMING


def prompt_modules() -> list[str]:
    """Every module in the package named `prompts`."""
    found = []
    for module in pkgutil.walk_packages(lumen.__path__, prefix="lumen."):
        if module.name.endswith(".prompts") and ".tests." not in module.name:
            found.append(module.name)
    return sorted(found)


class TestEveryStageNamesThePersonTheSameWay:
    """
    Their name is in their own writing, and models misspell it.

    The strings these prompts produce are the permanent record, so a
    misspelling is not cosmetic — it is somebody's name written wrongly in
    their own history, in a place they will read it back. Nothing needs the
    name: there is one person per deployment and nothing is retrieved by it.
    """

    def test_the_prompt_modules_are_actually_found(self):
        """A search that quietly matched nothing would pass every test below."""
        assert len(prompt_modules()) >= 5

    @pytest.mark.parametrize("module_name", prompt_modules())
    def test_the_system_instruction_carries_the_rule(self, module_name):
        module = importlib.import_module(module_name)
        instruction = getattr(module, "SYSTEM_INSTRUCTION", None)

        if instruction is None:
            pytest.skip(f"{module_name} defines no system instruction")

        assert AUTHOR_NAMING in instruction

    @pytest.mark.parametrize("module_name", prompt_modules())
    def test_the_rule_is_shared_rather_than_retyped(self, module_name):
        """
        A rule restated in five files disagrees with itself in one of them
        eventually. Importing the constant is what keeps the five in step.
        """
        module = importlib.import_module(module_name)
        if getattr(module, "SYSTEM_INSTRUCTION", None) is None:
            pytest.skip(f"{module_name} defines no system instruction")

        assert getattr(module, "AUTHOR_NAMING", None) is AUTHOR_NAMING


class TestWhatTheRuleSays:
    def test_it_names_what_to_use_instead(self):
        """A rule that forbids without offering a replacement gets ignored."""
        assert '"User"' in AUTHOR_NAMING

    def test_other_people_keep_their_names(self):
        """
        Who somebody else is is exactly what makes a record about them
        useful, so this narrows to the author alone.
        """
        assert "Other people keep their names" in AUTHOR_NAMING
