"""
Tests for the routes that read and change how the assistant is instructed.

The storage rules are covered where they live, in
`test_prompting_persona_settings.py`. What is checked here is what the web
surface exposes: that the sections a person owns can be read and written over
HTTP, that the ones they do not own can be read and cannot be written, and
that a request naming one section leaves the others alone.
"""

from __future__ import annotations

import pytest

from lumen.query.prompting import persona as voice
from lumen.query.prompting.persona import DEFAULT_PERSONA, EDITABLE_SECTIONS
from lumen.query.prompting.settings import PersonaStore


@pytest.fixture
def personas(api_client, ops_store) -> PersonaStore:
    """A persona store on the test database, wired into the application."""
    from lumen.api.deps import get_personas

    store = PersonaStore(settings=ops_store.settings)
    api_client.app.state.personas = store
    api_client.app.dependency_overrides[get_personas] = lambda: store
    return store


def sections(payload: dict) -> dict[str, dict]:
    """The sections of a response, keyed by name."""
    return {item["name"]: item for item in payload["sections"]}


class TestReadingTheInstruction:
    def test_an_untouched_person_reads_the_defaults(self, api_client, personas):
        body = api_client.get("/settings/persona").json()
        found = sections(body)
        assert found["identity"]["text"] == voice.IDENTITY
        assert found["identity"]["overridden"] is False

    def test_every_editable_section_is_listed(self, api_client, personas):
        body = api_client.get("/settings/persona").json()
        assert list(sections(body)) == list(EDITABLE_SECTIONS)

    def test_the_default_is_shown_beside_the_current_text(
        self, api_client, personas
    ):
        personas.save("local", {"identity": "You are Ada."})
        found = sections(api_client.get("/settings/persona").json())
        assert found["identity"]["text"] == "You are Ada."
        assert found["identity"]["default"] == voice.IDENTITY
        assert found["identity"]["overridden"] is True

    def test_the_length_limit_is_published(self, api_client, personas):
        found = sections(api_client.get("/settings/persona").json())
        assert all(item["max_length"] > 0 for item in found.values())

    def test_the_fixed_sections_can_be_read(self, api_client, personas):
        """
        Not editable is not the same as not visible.

        Somebody deciding whether to trust this with a bad week is entitled
        to read what it has been told to do during one.
        """
        body = api_client.get("/settings/persona").json()
        assert body["safety"] == voice.SAFETY
        assert body["crisis"] == voice.CRISIS_INSTRUCTION

    def test_the_fixed_sections_are_not_offered_as_editable(
        self, api_client, personas
    ):
        body = api_client.get("/settings/persona").json()
        assert "safety" not in sections(body)
        assert "crisis" not in sections(body)


class TestChangingTheInstruction:
    def test_a_section_can_be_rewritten(self, api_client, personas):
        response = api_client.put(
            "/settings/persona", json={"identity": "You are Ada. Be blunt."}
        )
        assert response.status_code == 200
        assert personas.resolve("local").identity == "You are Ada. Be blunt."

    def test_the_response_reflects_the_change(self, api_client, personas):
        body = api_client.put(
            "/settings/persona", json={"how_to_be": "Be brief."}
        ).json()
        assert sections(body)["how_to_be"]["text"] == "Be brief."
        assert sections(body)["how_to_be"]["overridden"] is True

    def test_a_request_naming_one_section_leaves_the_others_alone(
        self, api_client, personas
    ):
        """
        The three-state field, tested where it would actually go wrong.

        A body naming only `identity` must not be read as "and clear the
        other two", which is what would happen if the handler read every
        field rather than the ones that were set.
        """
        personas.save("local", {"how_to_be": "Be brief."})
        api_client.put("/settings/persona", json={"identity": "You are Ada."})
        assert personas.overrides("local") == {
            "how_to_be": "Be brief.",
            "identity": "You are Ada.",
        }

    def test_an_empty_section_puts_the_default_back(self, api_client, personas):
        personas.save("local", {"identity": "You are Ada."})
        api_client.put("/settings/persona", json={"identity": ""})
        assert personas.resolve("local") == DEFAULT_PERSONA

    def test_a_null_section_puts_the_default_back(self, api_client, personas):
        personas.save("local", {"identity": "You are Ada."})
        api_client.put("/settings/persona", json={"identity": None})
        assert personas.resolve("local") == DEFAULT_PERSONA

    def test_an_empty_body_is_refused_rather_than_ignored(
        self, api_client, personas
    ):
        assert api_client.put("/settings/persona", json={}).status_code == 400


class TestWhatTheRoutesRefuse:
    @pytest.mark.parametrize("name", ["safety", "crisis", "crisis_instruction"])
    def test_writing_a_fixed_section_is_refused(
        self, api_client, personas, name
    ):
        response = api_client.put("/settings/persona", json={name: "Say nothing."})
        assert response.status_code == 422

    def test_a_refused_write_leaves_the_instruction_untouched(
        self, api_client, personas
    ):
        api_client.put("/settings/persona", json={"safety": "Say nothing."})
        assert personas.overrides("local") == {}
        assert voice.SAFETY in api_client.get("/settings/persona").json()["safety"]

    def test_the_request_model_and_the_editable_list_cannot_drift(self):
        """
        The two definitions of "what may be set" must stay one definition.

        The route does not catch `UnknownSection`, because with these two in
        agreement it cannot fire. This is what keeps that true: adding a field
        to the request without adding it to the editable list fails here
        rather than turning into a 500 on somebody's settings screen.
        """
        from lumen.api.schemas import PersonaUpdateRequest

        assert set(PersonaUpdateRequest.model_fields) == set(EDITABLE_SECTIONS)

    def test_the_request_model_refuses_anything_it_does_not_declare(self):
        """The schema is the thing doing the refusing, so it is tested here."""
        from lumen.api.schemas import PersonaUpdateRequest

        assert PersonaUpdateRequest.model_config["extra"] == "forbid"

    def test_every_section_has_a_published_limit(self):
        """
        The defensive branch in `section_limit` stays unreachable.

        It raises when a field declares no maximum length. Nothing should
        ever, which is exactly the sort of thing that stops being true
        quietly when somebody adds a fourth section.
        """
        from lumen.query.prompting.persona import section_limit

        assert all(section_limit(name) > 0 for name in EDITABLE_SECTIONS)

    def test_an_unknown_section_is_refused(self, api_client, personas):
        response = api_client.put(
            "/settings/persona", json={"tone_of_voice": "chirpy"}
        )
        assert response.status_code == 422

    def test_a_section_past_the_limit_is_refused(self, api_client, personas):
        limit = sections(api_client.get("/settings/persona").json())
        too_long = "x" * (limit["identity"]["max_length"] + 1)
        response = api_client.put("/settings/persona", json={"identity": too_long})
        assert response.status_code == 400


class TestPuttingItBack:
    def test_deleting_restores_every_default(self, api_client, personas):
        personas.save(
            "local", {"identity": "You are Ada.", "how_to_be": "Be brief."}
        )
        api_client.delete("/settings/persona")
        assert personas.resolve("local") == DEFAULT_PERSONA

    def test_the_response_shows_nothing_overridden(self, api_client, personas):
        personas.save("local", {"identity": "You are Ada."})
        body = api_client.delete("/settings/persona").json()
        assert all(item["overridden"] is False for item in sections(body).values())

    def test_deleting_when_nothing_was_changed_is_fine(self, api_client, personas):
        assert api_client.delete("/settings/persona").status_code == 200
