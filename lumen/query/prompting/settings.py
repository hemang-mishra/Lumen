"""
Reading and writing the parts of the instruction a person has changed.

Only differences are stored. Somebody who has rewritten how the assistant
behaves but left the rest alone has one section on record, not three, and the
two they never touched keep following the defaults in `persona.py` as those
defaults improve. Storing the whole instruction on first edit would freeze a
copy of today's wording into their account forever, and every later
improvement would reach exactly the people who never cared enough to look.

The row is written under one key holding one object, rather than a key per
section. Three keys would make a partial write a real state — a save that
set two sections and failed on the third would leave an instruction nobody
composed — and the settings table has no transaction spanning keys.

Nothing here is cached. The read is a single primary-key lookup in a local
SQLite file on a turn that is about to make several model calls, so the cost
is not worth measuring; and a cache would mean an edit made through the API
not reaching a conversation already in progress in another process, which is
exactly the sort of thing somebody would spend an afternoon debugging.
"""

from __future__ import annotations

import logging

from pydantic import ValidationError

from lumen.operational.repositories import UserSettingsRepository
from lumen.query.prompting.persona import (
    DEFAULT_PERSONA,
    EDITABLE_SECTIONS,
    Persona,
    section_limit,
)

logger = logging.getLogger(__name__)

# Where the overrides live in the settings table.
PERSONA_KEY = "chat.persona"


class UnknownSection(ValueError):
    """Somebody tried to set a section that is not theirs to set."""


class SectionTooLong(ValueError):
    """A section was longer than one turn can afford to carry."""


class PersonaStore:
    """
    One person's instruction, resolved from their overrides.

    Holds the settings repository and nothing else. The defaults are not
    copied in at construction: they are read from `persona.py` at each
    resolve, so a deployment that updates its wording does not have to
    restart or migrate anything for the change to take.
    """

    def __init__(
        self,
        *,
        settings: UserSettingsRepository,
        default: Persona = DEFAULT_PERSONA,
    ) -> None:
        self._settings = settings
        self._default = default

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def resolve(self, user_id: str) -> Persona:
        """
        What this person's assistant is actually told.

        Never raises. This runs on the path to a reply, and an instruction
        that cannot be read is not a reason to refuse to talk to somebody —
        the defaults are a perfectly good assistant, which is the whole
        reason they are the defaults. A bad row is logged and stepped over.

        Built rather than copied over, so the stored sections go through the
        same checks a fresh one would. A row that got past the write limits
        some other way — an older version, a hand-edited database — is
        caught here rather than sent to a model.
        """
        stored = self._read(user_id)
        if not stored:
            return self._default

        try:
            return Persona(**{**self._default.model_dump(), **stored})
        except ValidationError:
            logger.warning(
                "this person's saved instruction could not be used, so the "
                "default one was",
                exc_info=True,
                extra={"user_id": user_id, "sections": sorted(stored)},
            )
            return self._default

    def overrides(self, user_id: str) -> dict[str, str]:
        """
        The sections this person has changed, and nothing else.

        Separate from `resolve` because the two answer different questions.
        A conversation wants the finished instruction; somebody looking at a
        settings screen wants to know which parts are theirs and which are
        still the defaults, and a merged object cannot tell them apart.
        """
        return dict(self._read(user_id))

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def save(self, user_id: str, sections: dict[str, str | None]) -> Persona:
        """
        Change some sections and leave the rest alone.

        A section set to `None`, to an empty string, or to nothing but
        whitespace is **cleared** rather than stored, and falls back to the
        default. That is the only sensible reading: an instruction section
        that is genuinely empty would put a heading with silence under it in
        front of the model, and clearing a box on a form is how a person says
        "go back to how it was".

        Raises:
            UnknownSection: A name that is not editable, including one of the
                safety sections. Refused rather than dropped — somebody who
                believes they have just rewritten the crisis instruction has
                to be told they have not.
            SectionTooLong: A section past the per-section limit.
        """
        unknown = sorted(set(sections) - set(EDITABLE_SECTIONS))
        if unknown:
            raise UnknownSection(
                f"these are not sections anybody can set: {', '.join(unknown)}"
            )

        current = dict(self._read(user_id))
        for name, value in sections.items():
            text = (value or "").strip()
            if not text:
                current.pop(name, None)
                continue
            _check_length(name, text)
            current[name] = text

        self._write(user_id, current)
        logger.info(
            "somebody changed how their assistant is instructed",
            extra={
                "user_id": user_id,
                "changed": sorted(sections),
                "now_overridden": sorted(current),
            },
        )
        return self.resolve(user_id)

    def reset(self, user_id: str, *names: str) -> Persona:
        """
        Put sections back to the defaults, or all of them when none is named.

        Resetting is deleting the override, not storing the default text. The
        difference shows up later: a section reset this way follows every
        future improvement to the wording, where one "reset" by pasting
        today's default back in would be frozen at today's default.
        """
        if not names:
            self._settings.delete(user_id, PERSONA_KEY)
            logger.info(
                "somebody put their whole instruction back to the default",
                extra={"user_id": user_id},
            )
            return self._default
        return self.save(user_id, {name: None for name in names})

    # ------------------------------------------------------------------
    # The stored row
    # ------------------------------------------------------------------

    def _read(self, user_id: str) -> dict[str, str]:
        """
        The overrides on record, with anything unusable dropped.

        Everything is checked on the way out as well as on the way in. The
        row is JSON in a general-purpose settings table, and the thing that
        wrote it will not always be the thing reading it — a hand-edited
        database, an older version of this code, a key somebody set by
        mistake. What must never happen is an unknown key reaching
        `model_copy`, which would raise on a turn somebody is waiting for.
        """
        try:
            raw = self._settings.get(user_id, PERSONA_KEY)
        except Exception:
            logger.warning(
                "could not read this person's saved instruction, so the "
                "default one is being used",
                exc_info=True,
                extra={"user_id": user_id},
            )
            return {}

        if not isinstance(raw, dict):
            if raw is not None:
                logger.warning(
                    "this person's saved instruction is not the right shape "
                    "and is being ignored",
                    extra={"user_id": user_id, "found": type(raw).__name__},
                )
            return {}

        return {
            name: value.strip()
            for name, value in raw.items()
            if name in EDITABLE_SECTIONS
            and isinstance(value, str)
            and value.strip()
        }

    def _write(self, user_id: str, overrides: dict[str, str]) -> None:
        """Store the overrides, or drop the row when there are none left."""
        if not overrides:
            self._settings.delete(user_id, PERSONA_KEY)
            return
        self._settings.set(user_id, PERSONA_KEY, dict(overrides))


def _check_length(name: str, text: str) -> None:
    """Refuse a section that would eat the turn's context on its own."""
    limit = section_limit(name)
    if len(text) > limit:
        raise SectionTooLong(
            f"{name} is {len(text)} characters, and the limit is {limit}"
        )


__all__ = [
    "PersonaStore",
    "PERSONA_KEY",
    "UnknownSection",
    "SectionTooLong",
]
