"""
Tests for the list of things an extraction is allowed to find.

The list is written out by hand while the categories themselves live in the
enum, so the two can drift apart. That drift is silent and expensive: a
category with no definition is one the model is never told about and so
never uses, and nobody finds out by reading the code.
"""

from __future__ import annotations

from lumen.pipeline.extraction.catalog import (
    EXCLUDED_TYPES,
    OBSERVATION_TYPE_DEFINITIONS,
    RAW_CAPTURE_TYPES,
    _CATEGORY_ORDER,
    allowed_types,
    render_type_dictionary,
)
from lumen.schemas.enums import ObservationType


class TestTheListIsComplete:
    def test_every_category_has_a_definition(self):
        assert set(OBSERVATION_TYPE_DEFINITIONS) == set(ObservationType)

    def test_every_category_is_shown_in_exactly_one_group(self):
        grouped = [member for _, members in _CATEGORY_ORDER for member in members]

        assert sorted(grouped, key=str) == sorted(ObservationType, key=str)
        assert len(grouped) == len(set(grouped))

    def test_no_definition_is_blank(self):
        assert all(text.strip() for text in OBSERVATION_TYPE_DEFINITIONS.values())

    def test_definitions_stay_one_line(self):
        multiline = [
            member
            for member, text in OBSERVATION_TYPE_DEFINITIONS.items()
            if "\n" in text
        ]

        assert multiline == []


class TestWhatGetsShownToTheModel:
    def test_the_dictionary_names_every_offered_category(self):
        rendered = render_type_dictionary()

        for member in ObservationType:
            if member not in EXCLUDED_TYPES:
                assert member.value in rendered

    def test_a_category_needing_audio_is_never_mentioned(self):
        # Naming it and forbidding it in one breath is an invitation to use
        # it. A category the model never sees is one it cannot reach for.
        rendered = render_type_dictionary()

        assert ObservationType.PROSODY_SIGNAL.value not in rendered

    def test_group_headings_survive_rendering(self):
        rendered = render_type_dictionary()

        for heading, _ in _CATEGORY_ORDER:
            assert heading in rendered

    def test_excluding_a_whole_group_drops_its_heading(self):
        relational = frozenset(
            {
                ObservationType.OTHER_PERSON_MODEL,
                ObservationType.RELATIONAL_DYNAMIC,
                ObservationType.GRATITUDE_APPRECIATION,
            }
        )

        rendered = render_type_dictionary(exclude=relational)

        assert "OTHER PEOPLE" not in rendered
        assert ObservationType.PATTERN.value in rendered


class TestWhatEachPathMayProduce:
    def test_a_close_reading_may_use_everything_but_audio(self):
        permitted = allowed_types(raw_capture=False)

        assert permitted == set(ObservationType) - EXCLUDED_TYPES

    def test_a_thin_entry_gets_only_topic_and_feeling(self):
        assert allowed_types(raw_capture=True) == RAW_CAPTURE_TYPES
        assert RAW_CAPTURE_TYPES == {ObservationType.CONTEXT, ObservationType.EMOTION}

    def test_the_thin_path_is_a_subset_of_the_full_one(self):
        assert allowed_types(raw_capture=True) <= allowed_types(raw_capture=False)
