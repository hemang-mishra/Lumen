"""
Tests for turning a person into a directory name.

The only place an identifier becomes part of a path, so the only place that
has to be careful about it. We generate these and they cannot contain
anything dangerous — and they are checked anyway, because "cannot" is a
property of today's generator and a directory traversal is permanent.

Every refusal below is a refusal rather than a correction. Cleaning an
identifier up would turn an invalid one into a valid-looking path, which
means somebody who should have caused an error quietly gets a directory
instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lumen.stores.keys import (
    UnsafeUserKey,
    collection_name,
    graph_dir,
    is_ours,
    user_key,
)


class TestWhatIsAllowed:
    def test_a_generated_identifier_passes(self):
        assert user_key("usr_9f2c4a1b8e3d") == "usr_9f2c4a1b8e3d"

    def test_it_comes_back_exactly_as_it_went_in(self):
        # A function that returned something different would be a second
        # identifier for the same person, and the directory their history
        # lives in cannot depend on which one a caller happened to use.
        for name in ("abc", "A-B_c", "usr_1", "x" * 64):
            assert user_key(name) == name

    def test_surrounding_space_is_trimmed_rather_than_refused(self):
        # Configuration and command lines add these; a trailing newline is
        # not somebody trying anything.
        assert user_key("  usr_1  ") == "usr_1"


class TestWhatIsRefused:
    @pytest.mark.parametrize(
        "attempt",
        [
            "../../etc/passwd",
            "..",
            "a/b",
            "a\\b",
            "/absolute",
            "with space",
            "semi;colon",
            "quote'd",
            "null\x00byte",
            "dot.dot",
        ],
    )
    def test_anything_that_could_leave_its_directory(self, attempt):
        with pytest.raises(UnsafeUserKey):
            user_key(attempt)

    def test_nothing_at_all(self):
        with pytest.raises(UnsafeUserKey):
            user_key("")

    def test_only_whitespace(self):
        with pytest.raises(UnsafeUserKey):
            user_key("   ")

    def test_something_far_too_long(self):
        with pytest.raises(UnsafeUserKey):
            user_key("x" * 65)

    def test_it_refuses_rather_than_cleaning_up(self):
        # The important distinction. A cleaned identifier is a valid path for
        # an invalid person.
        with pytest.raises(UnsafeUserKey):
            user_key("../usr_1")


class TestWhereThingsEndUp:
    def test_a_person_gets_a_directory_of_their_own(self):
        assert graph_dir("/data/graphs", "usr_1") == Path("/data/graphs/usr_1")

    def test_two_people_get_two_directories(self):
        assert graph_dir("/data", "usr_1") != graph_dir("/data", "usr_2")

    def test_a_path_can_never_escape_its_root(self):
        # Guaranteed by the validation rather than by anything about paths,
        # which is why this is the test that matters.
        with pytest.raises(UnsafeUserKey):
            graph_dir("/data/graphs", "../../../etc")

    def test_a_person_gets_a_collection_of_their_own(self):
        assert collection_name("usr_1") == "lumen_usr_1"

    def test_two_people_get_two_collections(self):
        assert collection_name("usr_1") != collection_name("usr_2")

    def test_a_collection_name_is_checked_the_same_way(self):
        with pytest.raises(UnsafeUserKey):
            collection_name("../evil")


class TestRecognisingOurOwn:
    def test_a_collection_we_made_is_recognised(self):
        assert is_ours(collection_name("usr_1")) is True

    def test_somebody_elses_is_not(self):
        # A search index shared with something else must not have its
        # collections mistaken for people.
        assert is_ours("someone_elses_data") is False
