"""
Tests for the decisions about whether somebody may have a session at all.

Everything below the service is about *how* a session is made. This is about
whether to make one: who is allowed an account, what happens when the same
person arrives from a second provider, and how fast somebody may keep trying.

The rule that runs through all of it is that a refusal never says whether an
account exists. "That address is not on the list" and "no such person" are the
same answer to whoever is asking, because the difference between them turns a
sign-in page into a way of finding out which addresses are worth trying.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from lumen.auth import keys as keymod
from lumen.auth.contracts import (
    ExternalIdentity,
    NotAuthenticated,
    SignInStart,
    SignUpRefused,
    TooManyAttempts,
)
from lumen.auth.limits import SignInLimiter
from lumen.auth.service import AuthService
from lumen.config import AuthConfig
from lumen.operational.enums import UserStatus

NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)


class Vouches:
    """A provider that vouches for whoever it was told to."""

    name = "GOOGLE"

    def __init__(self, *, subject="google-1", email="person@example.com", name_="A Person"):
        self._who = ExternalIdentity(
            provider="GOOGLE",
            subject=subject,
            email=email,
            email_verified=True,
            display_name=name_,
        )
        self.started = 0

    def start(self) -> SignInStart:
        self.started += 1
        return SignInStart(
            authorization_url="https://provider.example/auth",
            state="a-state",
            verifier="a-verifier",
        )

    def verify(self, code, verifier) -> ExternalIdentity:
        return self._who


@pytest.fixture
def keyset(monkeypatch):
    private, _ = keymod.generate()
    monkeypatch.setenv("LUMEN_JWT_PRIVATE_KEY", private)
    monkeypatch.delenv("LUMEN_JWT_PUBLIC_KEYS", raising=False)
    return keymod.load(AuthConfig())


@pytest.fixture
def build(ops_store, keyset):
    """An auth service over the real store and a provider that always agrees."""

    def _build(provider=None, **settings):
        config = AuthConfig(**settings)
        return (
            AuthService(
                repository=ops_store.identities,
                provider=provider or Vouches(),
                keys=keyset,
                config=config,
                limiter=SignInLimiter(config),
            ),
            ops_store,
        )

    return _build


class TestWhoIsAllowedAnAccount:
    def test_somebody_on_the_list_gets_one(self, build):
        auth, store = build(allowed_emails="person@example.com")

        identity, _ = auth.complete("code", "verifier", now=NOW)

        assert store.identities.find_user(identity.user_id)

    def test_somebody_who_is_not_is_refused(self, build):
        auth, store = build(allowed_emails="somebody-else@example.com")

        with pytest.raises(SignUpRefused):
            auth.complete("code", "verifier", now=NOW)

    def test_and_no_account_is_left_behind(self, build):
        auth, store = build(allowed_emails="somebody-else@example.com")

        with pytest.raises(SignUpRefused):
            auth.complete("code", "verifier", now=NOW)

        assert store.identities.find_by_email("person@example.com") is None

    def test_an_open_deployment_lets_anybody_in(self, build):
        auth, _ = build(signup_mode="open")

        identity, _ = auth.complete("code", "verifier", now=NOW)

        assert identity.email == "person@example.com"

    def test_a_misspelled_setting_is_restrictive_rather_than_permissive(self, build):
        # The direction a mistake should fail in when the cost of being wrong
        # is a stranger getting a database.
        auth, _ = build(signup_mode="opne", allowed_emails="")

        with pytest.raises(SignUpRefused):
            auth.complete("code", "verifier", now=NOW)

    def test_the_list_ignores_capitals_and_spaces(self, build):
        auth, _ = build(allowed_emails="  Person@Example.COM , other@example.com ")

        assert auth.complete("code", "verifier", now=NOW)[0].email == "person@example.com"


class TestComingBack:
    def test_the_second_sign_in_is_the_same_person(self, build):
        auth, _ = build(signup_mode="open")

        first, _ = auth.complete("code", "verifier", now=NOW)
        second, _ = auth.complete("code", "verifier", now=NOW)

        assert first.user_id == second.user_id

    def test_their_name_is_refreshed_from_the_provider(self, build, ops_store):
        # It is theirs to change and we are not the record of it.
        auth, _ = build(Vouches(name_="Old Name"), signup_mode="open")
        identity, _ = auth.complete("code", "verifier", now=NOW)

        renamed, _ = build(Vouches(name_="New Name"), signup_mode="open")
        renamed.complete("code", "verifier", now=NOW)

        assert ops_store.identities.find_user(identity.user_id).display_name == "New Name"

    def test_a_second_provider_links_rather_than_duplicating(self, build, ops_store):
        # Somebody moving from a personal to a work account should not end up
        # with two accounts holding half their history each.
        first, _ = build(Vouches(subject="google-1"), signup_mode="open")
        original, _ = first.complete("code", "verifier", now=NOW)

        second, _ = build(Vouches(subject="a-different-subject"), signup_mode="open")
        again, _ = second.complete("code", "verifier", now=NOW)

        assert again.user_id == original.user_id

    def test_being_seen_is_recorded(self, build, ops_store):
        auth, _ = build(signup_mode="open")

        identity, _ = auth.complete("code", "verifier", now=NOW)

        assert ops_store.identities.find_user(identity.user_id).last_seen_at is not None


class TestAnAccountThatIsNotActive:
    def test_a_suspended_person_cannot_sign_in(self, build, ops_store):
        auth, _ = build(signup_mode="open")
        identity, _ = auth.complete("code", "verifier", now=NOW)
        ops_store.identities.set_status(identity.user_id, UserStatus.SUSPENDED)

        with pytest.raises(NotAuthenticated, match="not active"):
            auth.complete("code", "verifier", now=NOW)

    def test_the_refusal_does_not_say_which_account(self, build, ops_store):
        auth, _ = build(signup_mode="open")
        identity, _ = auth.complete("code", "verifier", now=NOW)
        ops_store.identities.set_status(identity.user_id, UserStatus.SUSPENDED)

        with pytest.raises(NotAuthenticated) as refusal:
            auth.complete("code", "verifier", now=NOW)

        assert "person@example.com" not in str(refusal.value)


class TestSlowingDownASignIn:
    def test_a_caller_may_try_a_reasonable_number_of_times(self, build):
        auth, _ = build(signup_mode="open", signin_attempts=3)

        for _ in range(3):
            auth.start(caller="203.0.113.7", now=NOW)

    def test_and_then_is_asked_to_wait(self, build):
        auth, _ = build(signup_mode="open", signin_attempts=3)
        for _ in range(3):
            auth.start(caller="203.0.113.7", now=NOW)

        with pytest.raises(TooManyAttempts):
            auth.start(caller="203.0.113.7", now=NOW)

    def test_somebody_else_is_unaffected(self, build):
        auth, _ = build(signup_mode="open", signin_attempts=2)
        for _ in range(2):
            auth.start(caller="203.0.113.7", now=NOW)

        auth.start(caller="198.51.100.4", now=NOW)

    def test_the_window_passes(self, build):
        auth, _ = build(signup_mode="open", signin_attempts=2, signin_window_seconds=60)
        for _ in range(2):
            auth.start(caller="203.0.113.7", now=NOW)

        auth.start(caller="203.0.113.7", now=NOW + timedelta(seconds=61))

    def test_failed_attempts_on_one_address_count_across_callers(self, build):
        # Per caller catches one machine working through a list of addresses.
        # Per address catches a list of machines working on one account,
        # which looks like nothing at all if only callers are counted.
        auth, _ = build(allowed_emails="somebody-else@example.com", signin_attempts=2)

        for caller in ("203.0.113.7", "198.51.100.4"):
            with pytest.raises(SignUpRefused):
                auth.complete("code", "verifier", ip=caller, now=NOW)

        with pytest.raises(TooManyAttempts):
            auth.complete("code", "verifier", ip="192.0.2.9", now=NOW)

    def test_a_sign_in_that_works_is_not_counted_against_the_next_one(self, build):
        # Somebody using Lumen normally must never be rate limited for it.
        auth, _ = build(signup_mode="open", signin_attempts=2)

        for caller in ("203.0.113.7", "198.51.100.4", "192.0.2.9"):
            auth.complete("code", "verifier", ip=caller, now=NOW)

    def test_a_successful_sign_in_clears_the_count(self, build):
        # Somebody who mistyped their way through four attempts and then
        # succeeded should not be one attempt from being locked out.
        auth, _ = build(signup_mode="open", signin_attempts=3)
        auth.start(caller="203.0.113.7", now=NOW)
        auth.complete("code", "verifier", ip="203.0.113.7", now=NOW)

        for _ in range(3):
            auth.start(caller="203.0.113.7", now=NOW)

    def test_a_caller_nobody_can_identify_is_not_counted(self, build):
        # Better than counting them all together, which would let one
        # unidentifiable caller lock out every other one.
        auth, _ = build(signup_mode="open", signin_attempts=1)

        for _ in range(5):
            auth.start(caller=None, now=NOW)


class TestDecidingWhetherATokenIsStillGood:
    def test_a_fresh_one_identifies_its_person(self, build):
        auth, _ = build(signup_mode="open")
        identity, session = auth.complete("code", "verifier", now=NOW)

        assert auth.identify(session.access_token, now=NOW).user_id == identity.user_id

    def test_one_from_an_ended_session_is_refused(self, build):
        auth, _ = build(signup_mode="open")
        identity, session = auth.complete("code", "verifier", now=NOW)
        auth.end_every_session(identity.user_id, now=NOW)

        with pytest.raises(NotAuthenticated, match="ended"):
            auth.identify(session.access_token, now=NOW)

    def test_one_naming_somebody_who_no_longer_exists_is_refused(
        self, build, ops_store
    ):
        auth, _ = build(signup_mode="open")
        identity, session = auth.complete("code", "verifier", now=NOW)

        from sqlalchemy import delete
        from sqlalchemy.orm import Session

        from lumen.operational import models

        with Session(ops_store.engine) as db:
            db.execute(
                delete(models.User).where(models.User.user_id == identity.user_id)
            )
            db.commit()

        with pytest.raises(NotAuthenticated) as refusal:
            auth.identify(session.access_token, now=NOW)
        assert "no longer valid" in str(refusal.value)

    def test_one_for_a_suspended_person_is_refused(self, build, ops_store):
        auth, _ = build(signup_mode="open")
        identity, session = auth.complete("code", "verifier", now=NOW)
        ops_store.identities.set_status(identity.user_id, UserStatus.SUSPENDED)

        with pytest.raises(NotAuthenticated, match="not active"):
            auth.identify(session.access_token, now=NOW)


class TestAskingToBeForgotten:
    def test_it_ends_every_session_before_anything_else(self, build, ops_store):
        # In that order. Erasing while a session is live means requests
        # arriving for history that is disappearing underneath them.
        auth, _ = build(signup_mode="open")
        identity, session = auth.complete("code", "verifier", now=NOW)

        auth.begin_erasure(identity.user_id, now=NOW)

        with pytest.raises(NotAuthenticated):
            auth.identify(session.access_token, now=NOW)

    def test_it_marks_them_as_leaving(self, build, ops_store):
        auth, _ = build(signup_mode="open")
        identity, _ = auth.complete("code", "verifier", now=NOW)

        auth.begin_erasure(identity.user_id, now=NOW)

        stored = ops_store.identities.find_user(identity.user_id)
        assert stored.status is UserStatus.ERASURE_PENDING


class TestSmallerThings:
    def test_starting_a_sign_in_asks_the_provider(self, build):
        provider = Vouches()
        auth, _ = build(provider)

        auth.start(now=NOW)

        assert provider.started == 1

    def test_the_published_keys_come_from_the_key_set(self, build, keyset):
        auth, _ = build()

        assert auth.jwks == keyset.jwks()

    def test_somebody_can_be_described(self, build):
        auth, _ = build(signup_mode="open")
        identity, _ = auth.complete("code", "verifier", now=NOW)

        assert auth.describe(identity.user_id).email == "person@example.com"

    def test_describing_nobody_answers_nothing(self, build):
        auth, _ = build()

        assert auth.describe("usr_nobody") is None

    def test_signing_out_goes_through_to_the_session(self, build):
        auth, _ = build(signup_mode="open")
        _, session = auth.complete("code", "verifier", now=NOW)

        auth.sign_out(session.refresh_token, now=NOW)

        with pytest.raises(NotAuthenticated):
            auth.refresh(session.refresh_token, now=NOW)
