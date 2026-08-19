"""
What the assistant is told, and who it is while reading it.

The voice lives in `persona.py` and is the one file here meant to be read by
a person rather than by code. Everything else assembles it, and `settings.py`
is how a person replaces part of it with their own wording.
"""

from lumen.query.prompting.compose import PromptComposer
from lumen.query.prompting.contracts import ChatPrompt
from lumen.query.prompting.persona import DEFAULT_PERSONA, EDITABLE_SECTIONS, Persona
from lumen.query.prompting.settings import (
    PERSONA_KEY,
    PersonaStore,
    SectionTooLong,
    UnknownSection,
)
from lumen.query.prompting.system import build_system_prompt

__all__ = [
    "PromptComposer",
    "ChatPrompt",
    "build_system_prompt",
    "Persona",
    "DEFAULT_PERSONA",
    "EDITABLE_SECTIONS",
    "PersonaStore",
    "PERSONA_KEY",
    "UnknownSection",
    "SectionTooLong",
]
