"""
Tests for what an erasure replaces, and what it leaves alone.

The most valuable test in this file is the dullest: every kind of record has
to be listed. A new kind that nobody added would keep its text through an
erasure, and nothing would ever notice — no error, no failed run, just words
that were supposed to be gone.

The rest is about the two columns that cannot simply be overwritten. A list
must stay a list, and a record of what happened must keep the what and lose
only the words.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from lumen.graph import redaction
from lumen.graph.kuzu_impl import NODE_TABLES
from lumen.graph.queries import JSON_COLUMNS

AT = datetime(2026, 8, 20, 12, tzinfo=UTC)


class TestEveryKindIsAccountedFor:
    def test_no_kind_of_record_is_missing_from_the_rules(self):
        # The one that matters. A kind nobody listed keeps its text through
        # an erasure, silently.
        assert set(NODE_TABLES) == set(redaction.ERASABLE_COLUMNS)

    def test_every_column_named_actually_exists(self):
        # A misspelled column would be written to nothing and read as done.
        for table, columns in redaction.ERASABLE_COLUMNS.items():
            for column in columns:
                assert f"{column} " in NODE_TABLES[table], (table, column)

    def test_a_kind_nobody_has_heard_of_is_left_alone(self):
        # A sweep that met an unlisted table should leave it and let the
        # check above be what reports the omission. Refusing halfway through
        # would erase part of a history and stop.
        assert redaction.columns_for("SomethingNew") == ()
        assert redaction.holds_words("SomethingNew") is False


class TestReplacingWords:
    def test_a_plain_column_becomes_a_dated_marker(self):
        values = redaction.replacements_for("LessonNode", None, at=AT)

        assert values["lesson_statement"] == "[ERASED: 2026-08-20]"

    def test_a_list_column_stays_a_list(self):
        # Overwriting one with a bare sentence would leave a column that is
        # supposed to hold a list holding one, and every reader of that row
        # then has to cope with it.
        values = redaction.replacements_for("ObservationNode", None, at=AT)

        assert json.loads(values["raw_evidence"]) == ["[ERASED: 2026-08-20]"]

    def test_every_list_column_named_is_one_the_store_treats_as_a_list(self):
        assert redaction.LIST_COLUMNS <= JSON_COLUMNS

    def test_most_kinds_need_nothing_read_first(self):
        # Which is what lets a whole batch be rewritten in one statement.
        assert redaction.needs_the_row("PatternNode") is False
        assert redaction.needs_the_row("ObservationNode") is False


class TestPeople:
    def test_a_name_becomes_something_that_cannot_be_read_back(self):
        placeholder = redaction.person_placeholder("Alex")

        assert "Alex" not in placeholder
        assert placeholder.startswith("[ERASED_PERSON_")

    def test_the_same_person_always_becomes_the_same_stand_in(self):
        # Which is what keeps twelve mentions of one person looking like one
        # person, so the shape of somebody's relationships survives.
        assert redaction.person_placeholder("Alex") == redaction.person_placeholder("Alex")

    def test_two_people_stay_two_people(self):
        assert redaction.person_placeholder("Alex") != redaction.person_placeholder("Priya")

    def test_spacing_does_not_make_a_different_person(self):
        assert redaction.person_placeholder(" Alex ") == redaction.person_placeholder("Alex")

    def test_every_other_name_they_went_by_becomes_the_same_thing(self):
        # Keeping them apart would leak how many there were.
        values = redaction.replacements_for("PersonEntityNode", {"canonical_name": "Alex"}, at=AT)

        assert json.loads(values["aliases"]) == [redaction.ERASED_ALIAS]

    def test_what_they_were_to_the_person_goes_too(self):
        # "Manager" and "partner" say something about a life even with every
        # name gone.
        values = redaction.replacements_for("PersonEntityNode", {"canonical_name": "Alex"}, at=AT)

        assert values["relationship_to_user"] == "UNKNOWN"

    def test_a_person_record_has_to_be_read_first(self):
        assert redaction.needs_the_row("PersonEntityNode") is True


class TestRecordsInsideRecords:
    def test_the_shape_survives_and_the_words_do_not(self):
        stored = json.dumps(
            [
                {"state": "ACTIVE", "at": "2026-01-01", "reason": "decided to try it"},
                {"state": "RETIRED", "at": "2026-06-01", "reason": "it stopped helping"},
            ]
        )

        values = redaction.replacements_for(
            "AdoptedPrincipleNode", {"lifecycle_history": stored}, at=AT
        )
        history = json.loads(values["lifecycle_history"])

        assert [entry["state"] for entry in history] == ["ACTIVE", "RETIRED"]
        assert [entry["at"] for entry in history] == ["2026-01-01", "2026-06-01"]
        assert {entry["reason"] for entry in history} == {"[ERASED: 2026-08-20]"}

    def test_something_unreadable_is_emptied_rather_than_left(self):
        # A column nobody can parse is a column nobody can prove is empty of
        # words. This is the one operation where guessing the other way is
        # not acceptable.
        values = redaction.replacements_for(
            "AdoptedPrincipleNode", {"lifecycle_history": "not json at all"}, at=AT
        )

        assert json.loads(values["lifecycle_history"]) == []

    def test_anything_in_the_list_that_is_not_a_record_is_dropped(self):
        # A list holding a bare string is not a history of anything, and
        # keeping it would leave a value nobody downstream can read.
        stored = json.dumps(["a stray sentence", {"state": "ACTIVE", "reason": "kept"}])

        values = redaction.replacements_for(
            "AdoptedPrincipleNode", {"lifecycle_history": stored}, at=AT
        )
        history = json.loads(values["lifecycle_history"])

        assert history == [{"state": "ACTIVE", "reason": "[ERASED: 2026-08-20]"}]

    def test_a_missing_column_becomes_an_empty_list(self):
        values = redaction.replacements_for("AdoptedPrincipleNode", {}, at=AT)

        assert json.loads(values["lifecycle_history"]) == []
