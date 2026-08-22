"""
Reading and changing how the assistant is instructed to talk to somebody.

Three routes over one setting. What makes this worth its own file rather than
a couple of handlers bolted onto the chat router is what it touches: every
other write in this service adds to a record of what happened, and this one
changes what a model is told before it answers.

So it is narrow in a specific way. The sections a caller can set are the
three on `Persona`, and the two about distress are not fields on it. A
request naming one is refused at the schema — `PersonaUpdateRequest` declares
exactly the editable sections and forbids anything else — rather than quietly
ignored, because somebody who believes they have just rewritten the crisis
instruction has to find out here rather than from a conversation.

That is also why the store's `UnknownSection` is not caught below: it cannot
fire from here, because the request model and the editable list are asserted
to be the same set of names. If they ever drift, the test says so rather than
a handler swallowing it.

The fixed sections are still handed back to be read. Somebody deciding
whether to trust this with the worst week of their life is entitled to see
what it has been told to do in that week. Being unable to edit it is the
point; being unable to read it would be a different thing entirely.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from lumen.api.deps import get_config, get_personas, require_identity
from lumen.auth import Identity
from lumen.api.errors import BadRequest
from lumen.api.schemas import (
    PersonaSectionView,
    PersonaUpdateRequest,
    PersonaView,
)
from lumen.config import AppConfig
from lumen.query.prompting import persona as voice
from lumen.query.prompting.persona import EDITABLE_SECTIONS, Persona, section_limit
from lumen.query.prompting.settings import PersonaStore, SectionTooLong

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/persona", response_model=PersonaView)
def read_the_instruction(
    personas: PersonaStore = Depends(get_personas),
    config: AppConfig = Depends(get_config),
    identity: Identity = Depends(require_identity),
) -> PersonaView:
    """
    What the assistant is currently told, and what it would be told by default.

    Both, side by side, because a screen showing only the effective text
    cannot tell somebody whether they are looking at their own words or at
    what Lumen ships with — and that is the first thing anybody about to edit
    one of these needs to know.
    """
    return _view(
        personas.resolve(identity.user_id), personas.overrides(identity.user_id)
    )


@router.put("/persona", response_model=PersonaView)
def change_the_instruction(
    request: PersonaUpdateRequest,
    personas: PersonaStore = Depends(get_personas),
    config: AppConfig = Depends(get_config),
    identity: Identity = Depends(require_identity),
) -> PersonaView:
    """
    Rewrite one or more sections, leaving the others alone.

    A section the request does not mention is untouched; one sent empty or
    null is put back to the default. That distinction is why the request
    reports which fields were actually set rather than reading all three —
    a request naming one section must not silently clear the other two.
    """
    changes = request.changes()
    if not changes:
        raise BadRequest("this request does not change anything")

    try:
        personas.save(identity.user_id, changes)
    except SectionTooLong as exc:
        raise BadRequest(str(exc)) from exc

    return read_the_instruction(
        personas=personas, config=config, identity=identity
    )


@router.delete("/persona", response_model=PersonaView)
def put_the_instruction_back(
    personas: PersonaStore = Depends(get_personas),
    config: AppConfig = Depends(get_config),
    identity: Identity = Depends(require_identity),
) -> PersonaView:
    """
    Drop every override and go back to the wording Lumen ships with.

    Deleting rather than storing the current defaults, so a section put back
    this way keeps following later improvements to the wording instead of
    being frozen at whatever the default happened to say today.
    """
    personas.reset(identity.user_id)
    return read_the_instruction(
        personas=personas, config=config, identity=identity
    )


def _view(effective: Persona, overrides: dict[str, str]) -> PersonaView:
    """One person's instruction, in the shape a settings screen reads."""
    defaults = Persona()
    return PersonaView(
        sections=[
            PersonaSectionView(
                name=name,
                text=getattr(effective, name),
                default=getattr(defaults, name),
                overridden=name in overrides,
                max_length=section_limit(name),
            )
            for name in EDITABLE_SECTIONS
        ],
        safety=voice.SAFETY,
        crisis=voice.CRISIS_INSTRUCTION,
    )


__all__ = ["router"]
