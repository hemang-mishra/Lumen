"""
Tests for accepting what somebody comes back from Google with.

Five checks, and each is the whole security of sign-in. A Google assertion is
proof of nothing until it has been shown that Google signed it, that Google
issued it, that it was meant for this application, that it has not expired,
and that Google has confirmed the person controls the address.

The last one looks the least important and is not. An unconfirmed address is a
claim about a mailbox somebody may not own — accept it and a stranger can sign
in as whoever registers that address here later.

There is no network in this file. A real key signs a real token and the real
verification runs; only the transport is a stand-in.
"""

from __future__ import annotations

import pytest

from lumen.auth.contracts import NotAuthenticated
from lumen.auth.google import GoogleIdentityProvider
from lumen.config import AuthConfig
from lumen.tests.conftest_auth import FakeGoogleKeys, FakeTokenEndpoint, google_key, id_token


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "a-secret-nobody-should-see")
    return AuthConfig(
        google_client_id="client-1",
        google_redirect_uri="https://app.example/callback",
    )


@pytest.fixture
def google(settings):
    """Build a provider whose Google is a local key and a stub endpoint."""

    def _build(**overrides):
        key = google_key()
        token = id_token(key, client_id=settings.google_client_id, **overrides)
        endpoint = FakeTokenEndpoint(token)
        provider = GoogleIdentityProvider(
            settings, client=endpoint, keys=FakeGoogleKeys(key)
        )
        return provider, endpoint

    return _build


class TestSendingSomebodyToSignIn:
    def test_the_url_goes_to_google(self, google):
        provider, _ = google()

        assert provider.start().authorization_url.startswith(
            "https://accounts.google.com"
        )

    def test_it_carries_a_single_use_value_to_check_them_on_return(self, google):
        # Without it, a third party can complete a sign-in inside somebody
        # else's browser session.
        provider, _ = google()

        first = provider.start()
        second = provider.start()

        assert first.state and first.state != second.state

    def test_it_proves_possession_of_the_flow_that_started_it(self, google):
        provider, _ = google()

        url = provider.start().authorization_url

        assert "code_challenge=" in url
        assert "code_challenge_method=S256" in url

    def test_the_secret_is_not_in_the_url(self, google):
        # It is sent server-to-server and never to the browser.
        provider, _ = google()

        assert "a-secret-nobody-should-see" not in provider.start().authorization_url

    def test_the_verifier_is_not_in_the_url_either(self, google):
        provider, _ = google()

        start = provider.start()
        assert start.verifier not in start.authorization_url


class TestAcceptingWhatComesBack:
    def test_a_good_assertion_says_who_they_are(self, google):
        provider, _ = google()

        who = provider.verify("a-code", "a-verifier")

        assert who.provider == "GOOGLE"
        assert who.subject == "google-sub-1"
        assert who.email == "person@example.com"
        assert who.display_name == "A Person"

    def test_the_exchange_uses_the_secret_and_the_verifier(self, google):
        provider, endpoint = google()

        provider.verify("a-code", "a-verifier")

        sent = endpoint.calls[0]
        assert sent["client_secret"] == "a-secret-nobody-should-see"
        assert sent["code_verifier"] == "a-verifier"
        assert sent["grant_type"] == "authorization_code"


class TestTheFiveChecks:
    def test_an_unconfirmed_address_is_refused(self, google):
        # The one that looks least important. Accepting it would let a
        # stranger sign in as whoever registers that address here later.
        provider, _ = google(email_verified=False)

        with pytest.raises(NotAuthenticated, match="not been confirmed"):
            provider.verify("a-code", "a-verifier")

    def test_an_assertion_meant_for_another_application_is_refused(self, google):
        provider, _ = google(audience="somebody-elses-client-id")

        with pytest.raises(NotAuthenticated, match="another application"):
            provider.verify("a-code", "a-verifier")

    def test_an_assertion_from_somewhere_else_is_refused(self, google):
        provider, _ = google(issuer="https://accounts.evil.example")

        with pytest.raises(NotAuthenticated, match="somewhere unexpected"):
            provider.verify("a-code", "a-verifier")

    def test_an_expired_assertion_is_refused(self, google):
        provider, _ = google(expired=True)

        with pytest.raises(NotAuthenticated, match="took too long"):
            provider.verify("a-code", "a-verifier")

    def test_an_assertion_signed_by_somebody_else_is_refused(self, settings):
        # Correct in every other way, and signed with the wrong key.
        theirs = google_key()
        ours = google_key()
        provider = GoogleIdentityProvider(
            settings,
            client=FakeTokenEndpoint(id_token(theirs, client_id="client-1")),
            keys=FakeGoogleKeys(ours),
        )

        with pytest.raises(NotAuthenticated):
            provider.verify("a-code", "a-verifier")

    @pytest.mark.parametrize(
        "issuer", ["accounts.google.com", "https://accounts.google.com"]
    )
    def test_both_forms_google_states_itself_in_are_accepted(self, google, issuer):
        provider, _ = google(issuer=issuer)

        assert provider.verify("a-code", "a-verifier").subject == "google-sub-1"


class TestWhenTheExchangeItselfGoesWrong:
    def test_a_refused_exchange_is_a_refused_sign_in(self, settings):
        provider = GoogleIdentityProvider(
            settings,
            client=FakeTokenEndpoint(None, status=400),
            keys=FakeGoogleKeys(google_key()),
        )

        with pytest.raises(NotAuthenticated, match="could not be completed"):
            provider.verify("a-code", "a-verifier")

    def test_an_exchange_with_no_assertion_in_it_is_refused(self, settings):
        provider = GoogleIdentityProvider(
            settings,
            client=FakeTokenEndpoint(None),
            keys=FakeGoogleKeys(google_key()),
        )

        with pytest.raises(NotAuthenticated, match="proof of identity"):
            provider.verify("a-code", "a-verifier")

    def test_a_deployment_with_no_secret_cannot_sign_anybody_in(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
        provider = GoogleIdentityProvider(
            AuthConfig(google_client_id="client-1"),
            client=FakeTokenEndpoint("irrelevant"),
            keys=FakeGoogleKeys(google_key()),
        )

        with pytest.raises(NotAuthenticated, match="not configured"):
            provider.verify("a-code", "a-verifier")

    def test_keys_that_cannot_be_read_are_a_refusal_not_a_crash(self, settings):
        class Unreachable:
            def get_signing_key_from_jwt(self, token):
                raise RuntimeError("the key server is down")

        provider = GoogleIdentityProvider(
            settings,
            client=FakeTokenEndpoint(id_token(google_key(), client_id="client-1")),
            keys=Unreachable(),
        )

        with pytest.raises(NotAuthenticated, match="could not be verified"):
            provider.verify("a-code", "a-verifier")
