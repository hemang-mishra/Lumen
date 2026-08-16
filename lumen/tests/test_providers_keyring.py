"""
Tests for spreading requests across several credentials.

Nothing here talks to a vendor: a pool holds opaque strings, so these check
the choosing itself — that every key gets used, that a retry moves off the key
that just failed, and that a bad configuration is refused loudly.
"""

from __future__ import annotations

import random
from collections import Counter

import pytest

from lumen.providers.keyring import RANDOM, ROUND_ROBIN, ApiKeyPool

KEYS = ["key-one", "key-two", "key-three"]


class TestConstruction:
    def test_blank_entries_are_dropped(self):
        """A .env with a trailing comma should not create an empty slot."""
        pool = ApiKeyPool(["a", "", "  ", "b"])
        assert pool.keys == ("a", "b")

    def test_repeats_are_dropped(self):
        """
        The same key twice is one meter, not two. Keeping it would make the
        pool look larger than the quota it actually has.
        """
        pool = ApiKeyPool(["a", "b", "a"])
        assert pool.keys == ("a", "b")

    def test_configured_order_is_kept(self):
        assert ApiKeyPool(KEYS).keys == tuple(KEYS)

    def test_surrounding_whitespace_is_trimmed(self):
        assert ApiKeyPool([" a ", "\tb\n"]).keys == ("a", "b")

    def test_an_empty_pool_is_refused(self):
        with pytest.raises(ValueError, match="at least one key"):
            ApiKeyPool([])

    def test_a_pool_of_only_blanks_is_refused(self):
        with pytest.raises(ValueError, match="at least one key"):
            ApiKeyPool(["", "   "])

    def test_an_unknown_strategy_is_refused(self):
        """
        A typo in a deployment variable should stop the process, not quietly
        fall back to a default that hides it.
        """
        with pytest.raises(ValueError, match="unknown key rotation strategy"):
            ApiKeyPool(KEYS, strategy="round-robin")

    def test_length_reports_the_number_of_distinct_keys(self):
        assert len(ApiKeyPool(["a", "b", "a"])) == 2


class TestRandomStrategy:
    def test_it_is_the_default(self):
        assert ApiKeyPool(KEYS).strategy == RANDOM

    def test_every_key_gets_used(self):
        pool = ApiKeyPool(KEYS, random_source=random.Random(7))
        seen = Counter(pool.select() for _ in range(300))
        assert set(seen) == set(KEYS)

    def test_the_spread_is_roughly_even(self):
        """
        Not a distribution test — just enough to catch a pool that has quietly
        collapsed onto one key, which is the failure that would matter.
        """
        pool = ApiKeyPool(KEYS, random_source=random.Random(7))
        seen = Counter(pool.select() for _ in range(3000))
        assert all(600 < count < 1400 for count in seen.values())

    def test_it_holds_no_state_between_calls(self):
        """
        Two pools with the same seed agree, which is what "stateless across
        workers" means in practice: no shared counter has to be kept in step.
        """
        first = ApiKeyPool(KEYS, random_source=random.Random(11))
        second = ApiKeyPool(KEYS, random_source=random.Random(11))
        assert [first.select() for _ in range(10)] == [second.select() for _ in range(10)]


class TestRoundRobinStrategy:
    def test_it_walks_the_keys_in_order(self):
        pool = ApiKeyPool(KEYS, strategy=ROUND_ROBIN)
        assert [pool.select() for _ in range(4)] == [*KEYS, KEYS[0]]

    def test_the_spread_is_exactly_even(self):
        pool = ApiKeyPool(KEYS, strategy=ROUND_ROBIN)
        seen = Counter(pool.select() for _ in range(300))
        assert set(seen.values()) == {100}


class TestAvoidingAFailedKey:
    """
    The point of rotation: a call that died on a rate limit should go back out
    under a different key rather than pushing on the meter that is empty.
    """

    def test_the_excluded_key_is_not_chosen(self):
        pool = ApiKeyPool(KEYS, random_source=random.Random(3))
        assert all(pool.select(exclude="key-two") != "key-two" for _ in range(100))

    def test_round_robin_also_skips_it(self):
        pool = ApiKeyPool(KEYS, strategy=ROUND_ROBIN)
        assert all(pool.select(exclude="key-one") != "key-one" for _ in range(30))

    def test_round_robin_skips_a_turn_rather_than_shortening_the_list(self):
        """
        Indexing into a filtered list would shift every later position and
        turn an even walk into a lopsided one. Skipping keeps the order.
        """
        pool = ApiKeyPool(KEYS, strategy=ROUND_ROBIN)
        walked = [pool.select(exclude=None)]
        walked += [pool.select(exclude=walked[-1]) for _ in range(2)]
        assert walked == KEYS

    def test_a_single_key_is_still_returned(self):
        """
        Avoiding the only key would mean not calling at all, which is worse
        than retrying the same one after a wait.
        """
        pool = ApiKeyPool(["only"])
        assert pool.select(exclude="only") == "only"

    def test_a_key_the_pool_does_not_hold_excludes_nothing(self):
        pool = ApiKeyPool(KEYS, random_source=random.Random(5))
        assert {pool.select(exclude="stranger") for _ in range(200)} == set(KEYS)


class TestLabelling:
    """Log lines identify a key by where it sits, never by what it is."""

    def test_position_counts_from_one(self):
        pool = ApiKeyPool(KEYS)
        assert pool.position_of("key-one") == 1
        assert pool.position_of("key-three") == 3

    def test_an_unknown_key_has_no_position(self):
        assert ApiKeyPool(KEYS).position_of("stranger") == 0

    def test_the_label_says_which_of_how_many(self):
        assert ApiKeyPool(KEYS).label_for("key-two") == "2/3"

    def test_the_label_never_contains_the_key(self):
        pool = ApiKeyPool(KEYS)
        assert all(key not in pool.label_for(key) for key in KEYS)
