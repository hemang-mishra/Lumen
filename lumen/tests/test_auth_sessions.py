"""
Tests for handing out sessions, exchanging them, and ending them.

The reuse test is the one that matters. A refresh token lives for thirty days
and renews itself, so without detection a stolen one is a permanent
compromise that nobody ever finds out about. With it, the second use is the
moment everybody notices.

Run against the real store, because every claim here is about what a database
holds afterwards.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from lumen.auth import keys as keymod
from lumen.auth import tokens
from lumen.auth.contracts import Identity, NotAuthenticated, TokenReused
from lumen.auth.sessions import SessionService
from lumen.config import AuthConfig
from lumen.operational.schemas import hash_token

NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)


@pytest.fixture
def keyset(monkeypatch):
    private, _ = keymod.generate()
    monkeypatch.setenv("LUMEN_JWT_PRIVATE_KEY", private)
    monkeypatch.delenv("LUMEN_JWT_PUBLIC_KEYS", raising=False)
    return keymod.load(AuthConfig())


@pytest.fixture
def person(ops_store):
    """Somebody with an account."""
    return ops_store.identities.create_user(
        email="person@example.com", display_name="A Person", avatar_url=None
    )


@pytest.fixture
def sessions(ops_store, keyset):
    """The session service over the real store."""
    return SessionService(ops_store.identities, keys=keyset, config=AuthConfig())


def who(person) -> Identity:
    """The stored person as the identity a session is issued to."""
    return Identity(
        user_id=person.user_id, email=person.email, token_version=person.token_version
    )


class TestStartingOne:
    def test_it_hands_back_both_halves(self, sessions, person):
        pair = sessions.issue(who(person), now=NOW)

        assert pair.access_token
        assert pair.refresh_token
        assert pair.expires_in > 0

    def test_the_long_half_is_stored_as_a_hash_and_not_as_itself(
        self, sessions, person, ops_store
    ):
        # Reading the database tells you who has a session. It does not let
        # you become them.
        pair = sessions.issue(who(person), now=NOW)

        assert ops_store.identities.find_session(hash_token(pair.refresh_token))
        assert ops_store.identities.find_session(pair.refresh_token) is None

    def test_it_remembers_where_the_session_was_started(
        self, sessions, person, ops_store
    ):
        pair = sessions.issue(who(person), agent="Firefox", ip="203.0.113.7", now=NOW)
        stored = ops_store.identities.find_session(hash_token(pair.refresh_token))

        assert stored.user_agent == "Firefox"

    def test_two_sessions_are_two_different_tokens(self, sessions, person):
        first = sessions.issue(who(person), now=NOW)
        second = sessions.issue(who(person), now=NOW)

        assert first.refresh_token != second.refresh_token


class TestExchangingOne:
    def test_it_gives_a_new_pair(self, sessions, person):
        first = sessions.issue(who(person), now=NOW)

        second = sessions.rotate(first.refresh_token, now=NOW)

        assert second.refresh_token != first.refresh_token
        assert second.access_token != first.access_token

    def test_the_old_one_records_what_replaced_it(
        self, sessions, person, ops_store
    ):
        first = sessions.issue(who(person), now=NOW)
        second = sessions.rotate(first.refresh_token, now=NOW)

        old = ops_store.identities.find_session(hash_token(first.refresh_token))
        new = ops_store.identities.find_session(hash_token(second.refresh_token))
        assert old.rotated_to == new.token_id

    def test_the_new_one_is_usable_and_the_old_one_is_not(
        self, sessions, person, ops_store
    ):
        first = sessions.issue(who(person), now=NOW)
        second = sessions.rotate(first.refresh_token, now=NOW)

        assert ops_store.identities.find_session(
            hash_token(second.refresh_token)
        ).usable_at(NOW)
        assert not ops_store.identities.find_session(
            hash_token(first.refresh_token)
        ).usable_at(NOW)

    def test_a_token_nobody_recognises_is_refused(self, sessions):
        with pytest.raises(NotAuthenticated, match="not recognised"):
            sessions.rotate("nothing-like-a-real-token", now=NOW)

    def test_an_expired_one_is_refused(self, sessions, person):
        first = sessions.issue(who(person), now=NOW)
        much_later = NOW + timedelta(days=400)

        with pytest.raises(NotAuthenticated):
            sessions.rotate(first.refresh_token, now=much_later)

    def test_a_suspended_account_cannot_renew(self, sessions, person, ops_store):
        from lumen.operational.enums import UserStatus

        first = sessions.issue(who(person), now=NOW)
        ops_store.identities.set_status(person.user_id, UserStatus.SUSPENDED)

        with pytest.raises(NotAuthenticated, match="not active"):
            sessions.rotate(first.refresh_token, now=NOW)


class TestUsingOneTwice:
    def test_it_is_refused(self, sessions, person):
        first = sessions.issue(who(person), now=NOW)
        sessions.rotate(first.refresh_token, now=NOW)

        with pytest.raises(TokenReused):
            sessions.rotate(first.refresh_token, now=NOW)

    def test_it_ends_the_whole_chain_and_not_just_that_token(
        self, sessions, person, ops_store
    ):
        # By the time a reused token shows up, whoever else has it may
        # already be holding something newer. Ending only what was presented
        # would leave them signed in.
        first = sessions.issue(who(person), now=NOW)
        second = sessions.rotate(first.refresh_token, now=NOW)
        third = sessions.rotate(second.refresh_token, now=NOW)

        with pytest.raises(TokenReused):
            sessions.rotate(first.refresh_token, now=NOW)

        for pair in (first, second, third):
            stored = ops_store.identities.find_session(hash_token(pair.refresh_token))
            assert stored.revoked_at is not None

    def test_it_also_ends_the_short_lived_tokens_already_out_there(
        self, sessions, person, ops_store, keyset
    ):
        # Otherwise a thief keeps working for another fifteen minutes after
        # being caught.
        first = sessions.issue(who(person), now=NOW)
        second = sessions.rotate(first.refresh_token, now=NOW)
        before = tokens.verify(
            second.access_token, keys=keyset, config=AuthConfig(), now=NOW
        )

        with pytest.raises(TokenReused):
            sessions.rotate(first.refresh_token, now=NOW)

        after = ops_store.identities.find_user(person.user_id)
        assert before.token_version != after.token_version

    def test_a_chain_that_has_been_ended_cannot_be_continued(
        self, sessions, person
    ):
        first = sessions.issue(who(person), now=NOW)
        second = sessions.rotate(first.refresh_token, now=NOW)

        with pytest.raises(TokenReused):
            sessions.rotate(first.refresh_token, now=NOW)
        with pytest.raises(NotAuthenticated):
            sessions.rotate(second.refresh_token, now=NOW)


class TestEndingOne:
    def test_signing_out_ends_this_session(self, sessions, person, ops_store):
        pair = sessions.issue(who(person), now=NOW)

        sessions.revoke(pair.refresh_token, now=NOW)

        stored = ops_store.identities.find_session(hash_token(pair.refresh_token))
        assert stored.revoked_at is not None

    def test_signing_out_leaves_the_other_devices_alone(
        self, sessions, person, ops_store
    ):
        # Signing out on a library computer means this device. Ending
        # somebody's phone session too would be surprising.
        laptop = sessions.issue(who(person), now=NOW)
        phone = sessions.issue(who(person), now=NOW)

        sessions.revoke(laptop.refresh_token, now=NOW)

        assert ops_store.identities.find_session(
            hash_token(phone.refresh_token)
        ).usable_at(NOW)

    def test_signing_out_of_something_that_is_not_a_session_is_fine(self, sessions):
        # Already in the state being asked for.
        sessions.revoke("nothing-like-a-real-token", now=NOW)

    def test_losing_a_device_ends_everything(self, sessions, person, ops_store):
        laptop = sessions.issue(who(person), now=NOW)
        phone = sessions.issue(who(person), now=NOW)

        ended = sessions.revoke_everything(person.user_id, now=NOW)

        assert ended == 2
        for pair in (laptop, phone):
            assert not ops_store.identities.find_session(
                hash_token(pair.refresh_token)
            ).usable_at(NOW)

    def test_losing_a_device_also_ends_the_short_lived_tokens(
        self, sessions, person, ops_store
    ):
        before = ops_store.identities.find_user(person.user_id).token_version

        sessions.revoke_everything(person.user_id, now=NOW)

        assert ops_store.identities.find_user(person.user_id).token_version > before
