"""
Tests for the stand-in embedder that recognises a theme.

The property everything else depends on is the third one here: two entries
about the same thing must always be closer to each other than to an entry
about something else, whatever their wording. If the wobble can overturn a
theme match, the multi-day test stops proving anything and starts depending
on which sentences happened to hash favourably.
"""

from __future__ import annotations

import math

import pytest

from lumen.simulation.themes import Theme, ThemedEmbeddingProvider

COMPARISON = Theme(
    name="comparison",
    keywords=("comparing", "compare", "behind", "ahead of me", "measuring myself"),
)
SLEEP = Theme(name="sleep", keywords=("sleep", "slept", "tired", "exhausted"))
COOKING = Theme(name="cooking", keywords=("cooked", "kitchen", "dinner"))

THEMES = (COMPARISON, SLEEP, COOKING)


@pytest.fixture
def embedder() -> ThemedEmbeddingProvider:
    return ThemedEmbeddingProvider(THEMES, dimensions=64)


def closeness(a: list[float], b: list[float]) -> float:
    """How alike two vectors are, from -1 to 1."""
    return sum(x * y for x, y in zip(a, b, strict=True))


class TestRecognisingATheme:
    def test_the_words_registered_against_a_theme_find_it(self, embedder):
        assert embedder.themes_in("I keep comparing myself to him") == ("comparison",)

    def test_text_can_touch_more_than_one(self, embedder):
        found = embedder.themes_in("I was too tired to stop comparing")

        assert set(found) == {"comparison", "sleep"}

    def test_text_about_nothing_registered_touches_nothing(self, embedder):
        assert embedder.themes_in("The bus was late again") == ()


class TestWhereTextLands:
    def test_the_same_words_always_land_in_the_same_place(self, embedder):
        assert embedder.vector_for("I felt behind") == embedder.vector_for("I felt behind")

    def test_two_entries_on_one_theme_land_close(self, embedder):
        monday = embedder.vector_for("Seeing his work left me feeling behind")
        friday = embedder.vector_for("There it was again, that comparing")

        assert closeness(monday, friday) > 0.8

    def test_two_entries_on_different_themes_land_far_apart(self, embedder):
        comparing = embedder.vector_for("I keep comparing myself")
        cooking = embedder.vector_for("I cooked a proper dinner")

        assert closeness(comparing, cooking) < 0.5

    def test_same_theme_always_beats_different_theme(self, embedder):
        # The one property everything using this relies on. If the wording
        # can overturn a theme match, the multi-day test stops proving
        # anything and starts depending on which sentences hashed well.
        on_theme = [
            "I keep comparing myself to everyone",
            "that feeling of being behind again",
            "measuring myself against him all evening",
            "I compare and it never helps",
        ]
        off_theme = [
            "I cooked a proper dinner",
            "the kitchen was a mess",
            "I slept badly",
            "exhausted by four",
        ]

        for text in on_theme:
            here = embedder.vector_for(text)
            nearest_same = min(
                closeness(here, embedder.vector_for(other))
                for other in on_theme
                if other != text
            )
            furthest_other = max(
                closeness(here, embedder.vector_for(other)) for other in off_theme
            )
            assert nearest_same > furthest_other

    def test_two_entries_on_one_theme_are_still_distinguishable(self, embedder):
        # Close is not the same as identical. Two entries that landed on
        # exactly the same point could not be ranked against each other.
        first = embedder.vector_for("Seeing his work left me feeling behind")
        second = embedder.vector_for("There it was again, that comparing")

        assert closeness(first, second) < 0.999

    def test_text_about_nothing_registered_falls_back_to_its_wording(self, embedder):
        # Unrelated filler must not drift together, or everything in a long
        # entry ends up looking related to everything else.
        first = embedder.vector_for("The bus was late again")
        second = embedder.vector_for("I need to renew the parking permit")

        assert closeness(first, second) < 0.5


class TestTheVectorsThemselves:
    def test_they_are_the_width_that_was_asked_for(self, embedder):
        assert len(embedder.vector_for("anything")) == 64

    def test_they_have_a_length_of_one(self, embedder):
        # The same shape a real embedding model produces, which is what
        # keeps closeness meaningful.
        vector = embedder.vector_for("I keep comparing myself")

        assert math.isclose(math.sqrt(sum(v * v for v in vector)), 1.0, rel_tol=1e-9)

    def test_a_batch_comes_back_in_the_order_it_was_given(self, embedder):
        texts = ["I felt behind", "I cooked dinner", "I slept badly"]

        assert embedder.embed_batch(texts) == [
            embedder.vector_for(text) for text in texts
        ]

    def test_it_remembers_what_it_was_asked_to_embed(self, embedder):
        embedder.embed_batch(["one", "two"])

        assert embedder.embedded == ["one", "two"]

    def test_closing_it_is_noticed(self, embedder):
        embedder.close()

        assert embedder.closed is True


class TestStability:
    def test_a_theme_sits_in_the_same_place_in_a_fresh_provider(self):
        # Worked out from the theme's name rather than from anything about
        # the running process, so a test that depends on which of two things
        # is closer gives the same answer everywhere.
        first = ThemedEmbeddingProvider(THEMES, dimensions=64)
        second = ThemedEmbeddingProvider(THEMES, dimensions=64)

        assert first.vector_for("feeling behind") == second.vector_for("feeling behind")

    def test_a_provider_with_no_themes_behaves_like_the_plain_one(self):
        bare = ThemedEmbeddingProvider(dimensions=64)

        assert bare.themes_in("I keep comparing myself") == ()
        assert len(bare.vector_for("anything")) == 64

    def test_the_wobble_can_be_turned_down(self):
        # At zero, two entries on one theme are indistinguishable. Useful for
        # showing what the wobble is actually for.
        rigid = ThemedEmbeddingProvider(THEMES, dimensions=64, wobble=0.0)

        assert rigid.vector_for("comparing again") == rigid.vector_for("felt behind")
