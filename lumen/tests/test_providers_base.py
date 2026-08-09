"""
Tests for the shared parts of a model call.

Everything here is behaviour that every provider inherits: reading JSON,
splitting a batch up, working out how wide a model's vectors are, and refusing
to guess when that is not known.
"""

from __future__ import annotations

import pytest

from lumen.providers.base import (
    KNOWN_EMBEDDING_DIMENSIONS,
    _attempts_of,
    _chunked,
    _parse_json,
    normalise_model_name,
    resolve_dimensions,
)
from lumen.providers.errors import ProviderConfigurationError, ProviderTimeoutError


class TestReadingJson:
    def test_a_plain_object_is_read(self):
        assert _parse_json('{"a": 1}') == ({"a": 1}, None)

    def test_surrounding_whitespace_is_ignored(self):
        data, error = _parse_json('\n  {"a": 1}  \n')
        assert data == {"a": 1}
        assert error is None

    def test_broken_json_is_described_rather_than_raised(self):
        data, error = _parse_json("{not json")
        assert data is None
        assert "invalid JSON" in error

    def test_empty_text_is_described(self):
        data, error = _parse_json("   ")
        assert data is None
        assert error == "response was empty"

    @pytest.mark.parametrize("text,kind", [("[1, 2]", "list"), ("42", "int"), ('"hi"', "str")])
    def test_json_that_is_not_an_object_is_refused(self, text, kind):
        """A caller asked for an object and needs to know it did not get one."""
        data, error = _parse_json(text)
        assert data is None
        assert kind in error

    def test_nested_objects_are_fine(self):
        data, _ = _parse_json('{"outer": {"inner": [1, 2]}}')
        assert data == {"outer": {"inner": [1, 2]}}


class TestSplittingBatches:
    def test_an_exact_multiple_splits_evenly(self):
        assert _chunked(["a", "b", "c", "d"], 2) == [["a", "b"], ["c", "d"]]

    def test_a_remainder_becomes_a_shorter_last_chunk(self):
        assert _chunked(["a", "b", "c"], 2) == [["a", "b"], ["c"]]

    def test_a_batch_smaller_than_the_size_stays_whole(self):
        assert _chunked(["a"], 32) == [["a"]]

    def test_an_empty_batch_gives_nothing(self):
        assert _chunked([], 4) == []

    def test_a_size_of_zero_is_treated_as_one(self):
        """Rather than dividing by zero or looping forever."""
        assert _chunked(["a", "b"], 0) == [["a"], ["b"]]


class TestModelNames:
    def test_a_version_tag_is_dropped(self):
        assert normalise_model_name("nomic-embed-text:v1.5") == "nomic-embed-text"

    def test_a_name_without_a_tag_is_unchanged(self):
        assert normalise_model_name("text-embedding-004") == "text-embedding-004"

    def test_surrounding_space_is_trimmed(self):
        assert normalise_model_name("  phi-3  ") == "phi-3"


class TestVectorWidths:
    def test_a_known_model_reports_its_width(self):
        assert resolve_dimensions("text-embedding-004") == 768

    def test_a_tagged_known_model_still_resolves(self):
        assert resolve_dimensions("nomic-embed-text:latest") == 768

    def test_an_unknown_model_is_refused(self):
        """
        The tempting shortcut is to fall back to whatever width the vector store
        expects. That would make the check comparing the two meaningless, since
        they would always agree, and a genuinely mismatched model would only fail
        much later when a write was rejected.
        """
        with pytest.raises(ProviderConfigurationError, match="not known"):
            resolve_dimensions("some-brand-new-model")

    def test_the_refusal_names_the_model(self):
        with pytest.raises(ProviderConfigurationError, match="some-brand-new-model"):
            resolve_dimensions("some-brand-new-model")

    def test_the_refusal_says_what_to_do(self):
        """A message that only says no leaves somebody stuck."""
        with pytest.raises(ProviderConfigurationError, match="LUMEN_EMBEDDING_DIMENSIONS"):
            resolve_dimensions("some-brand-new-model")

    def test_the_refusal_explains_itself(self):
        with pytest.raises(ProviderConfigurationError, match="defeat the check"):
            resolve_dimensions("some-brand-new-model")

    def test_a_stated_width_is_accepted_for_an_unknown_model(self):
        """Somebody who knows the width can say so instead of being blocked."""
        assert resolve_dimensions("some-brand-new-model", expected=1536) == 1536

    def test_a_known_model_beats_a_stated_width(self):
        assert resolve_dimensions("text-embedding-004", expected=1536) == 768

    def test_both_default_models_are_known(self):
        """So neither default configuration is refused on startup."""
        assert "text-embedding-004" in KNOWN_EMBEDDING_DIMENSIONS
        assert "nomic-embed-text" in KNOWN_EMBEDDING_DIMENSIONS

    def test_the_two_defaults_are_the_same_width(self):
        """Which is what makes swapping one for the other a config change."""
        assert (
            KNOWN_EMBEDDING_DIMENSIONS["text-embedding-004"]
            == KNOWN_EMBEDDING_DIMENSIONS["nomic-embed-text"]
        )


class TestCountingAttempts:
    def test_a_provider_failure_reports_its_own_count(self):
        error = ProviderTimeoutError("too slow")
        error.attempts = 4
        assert _attempts_of(error) == 4

    def test_anything_else_counts_as_one_attempt(self):
        assert _attempts_of(RuntimeError("unrelated")) == 1

    def test_no_failure_counts_as_one_attempt(self):
        assert _attempts_of(None) == 1
