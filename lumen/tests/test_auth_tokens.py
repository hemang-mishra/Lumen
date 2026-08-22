"""
Tests for deciding whether a session token is genuine.

Every check here has been somebody's breach. The algorithm one in particular:
a verifier that accepts whichever algorithm a token's own header names can be
handed one saying "signed with HMAC, using the public key as the secret" — and
the public key is published.

Nothing in this file touches a store or waits for a clock. That is the point
of a module that only knows how to sign and check.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from lumen.auth import keys as keymod
from lumen.auth import tokens
from lumen.auth.contracts import Identity, NotAuthenticated
from lumen.config import AuthConfig

NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)


@pytest.fixture
def keyset(monkeypatch):
    """A fresh signing key, configured the way a deployment configures one."""
    private, _ = keymod.generate()
    monkeypatch.setenv("LUMEN_JWT_PRIVATE_KEY", private)
    monkeypatch.delenv("LUMEN_JWT_PUBLIC_KEYS", raising=False)
    return keymod.load(AuthConfig())


@pytest.fixture
def settings():
    """Ordinary token settings."""
    return AuthConfig()


@pytest.fixture
def somebody():
    """A person to mint a token for."""
    return Identity(user_id="usr_1", email="person@example.com", token_version=3)


class TestAGoodToken:
    def test_it_says_who_it_is_for(self, keyset, settings, somebody):
        token, _ = tokens.mint(somebody, keys=keyset, config=settings, now=NOW)

        claims = tokens.verify(token, keys=keyset, config=settings, now=NOW)

        assert claims.user_id == "usr_1"
        assert claims.email == "person@example.com"

    def test_it_carries_which_generation_of_session_it_is(
        self, keyset, settings, somebody
    ):
        # The one thing a signature cannot tell you: whether the session has
        # since been ended. This is what gets compared against the store.
        token, _ = tokens.mint(somebody, keys=keyset, config=settings, now=NOW)

        assert tokens.verify(token, keys=keyset, config=settings, now=NOW).token_version == 3

    def test_it_says_how_long_it_is_good_for(self, keyset, settings, somebody):
        _, lifetime = tokens.mint(somebody, keys=keyset, config=settings, now=NOW)

        assert lifetime == settings.access_ttl_seconds

    def test_each_one_is_distinguishable_from_the_last(self, keyset, settings, somebody):
        first, _ = tokens.mint(somebody, keys=keyset, config=settings, now=NOW)
        second, _ = tokens.mint(somebody, keys=keyset, config=settings, now=NOW)

        assert first != second

    def test_it_names_the_key_that_signed_it(self, keyset, settings, somebody):
        # Which is what makes rotating a key possible without an outage.
        token, _ = tokens.mint(somebody, keys=keyset, config=settings, now=NOW)

        assert jwt.get_unverified_header(token)["kid"] == keyset.signing_kid


class TestABadToken:
    def test_nothing_at_all(self, keyset, settings):
        with pytest.raises(NotAuthenticated, match="no token"):
            tokens.verify("", keys=keyset, config=settings)

    def test_something_that_is_not_a_token(self, keyset, settings):
        with pytest.raises(NotAuthenticated):
            tokens.verify("not-a-token", keys=keyset, config=settings)

    def test_one_that_has_been_tampered_with(self, keyset, settings, somebody):
        token, _ = tokens.mint(somebody, keys=keyset, config=settings, now=NOW)

        with pytest.raises(NotAuthenticated, match="signature"):
            tokens.verify(token + "x", keys=keyset, config=settings, now=NOW)

    def test_one_signed_by_somebody_else(self, keyset, settings, somebody, monkeypatch):
        # A perfectly well-formed token from a different key.
        other_private, _ = keymod.generate()
        monkeypatch.setenv("LUMEN_JWT_PRIVATE_KEY", other_private)
        theirs = keymod.load(AuthConfig())
        token, _ = tokens.mint(somebody, keys=theirs, config=settings, now=NOW)

        with pytest.raises(NotAuthenticated):
            tokens.verify(token, keys=keyset, config=settings, now=NOW)

    def test_one_that_has_expired(self, keyset, settings, somebody):
        token, _ = tokens.mint(somebody, keys=keyset, config=settings, now=NOW)

        with pytest.raises(NotAuthenticated, match="expired"):
            tokens.verify(
                token, keys=keyset, config=settings, now=NOW + timedelta(hours=2)
            )

    def test_one_issued_for_something_else(self, keyset, settings, somebody):
        # A valid signature over the wrong claim: minted by a system sharing
        # our key, for an audience that is not this API.
        token, _ = tokens.mint(somebody, keys=keyset, config=settings, now=NOW)

        with pytest.raises(NotAuthenticated, match="issued for something else"):
            tokens.verify(
                token,
                keys=keyset,
                config=AuthConfig(audience="somebody-elses-api"),
                now=NOW,
            )

    def test_one_issued_by_something_else(self, keyset, settings, somebody):
        token, _ = tokens.mint(
            somebody, keys=keyset, config=AuthConfig(issuer="not-lumen"), now=NOW
        )

        with pytest.raises(NotAuthenticated, match="issued by something else"):
            tokens.verify(token, keys=keyset, config=settings, now=NOW)

    def test_one_naming_a_key_nothing_here_holds(self, keyset, settings, somebody):
        other_private, _ = keymod.generate()
        import os

        os.environ["LUMEN_JWT_PRIVATE_KEY"] = other_private
        theirs = keymod.load(AuthConfig())
        token, _ = tokens.mint(somebody, keys=theirs, config=settings, now=NOW)

        with pytest.raises(NotAuthenticated, match="signed by something unknown"):
            tokens.verify(token, keys=keyset, config=settings, now=NOW)


def _forge_hmac(header: dict, claims: dict, *, secret: bytes) -> str:
    """
    A token signed with HMAC over a published key, assembled by hand.

    This is the algorithm-confusion attack in full: the signing key is the
    public key everybody can fetch, and the token asks to be verified with an
    algorithm that treats it as a shared secret.
    """
    import base64
    import hashlib
    import hmac
    import json as _json

    def part(value: dict) -> bytes:
        return base64.urlsafe_b64encode(
            _json.dumps(value, separators=(",", ":")).encode()
        ).rstrip(b"=")

    signed = part(header) + b"." + part(claims)
    signature = base64.urlsafe_b64encode(
        hmac.new(secret, signed, hashlib.sha256).digest()
    ).rstrip(b"=")
    return (signed + b"." + signature).decode()


class TestTheAlgorithmCannotBeChosenByTheToken:
    def test_a_token_asking_for_a_symmetric_algorithm_is_refused(
        self, keyset, settings
    ):
        # The attack this prevents: sign a token with HMAC, using the
        # published public key as the shared secret, and hand it to a
        # verifier that reads the algorithm out of the token.
        # Built by hand rather than with a library, because a library
        # refuses to make one — and an attacker is not using our library.
        from cryptography.hazmat.primitives import serialization

        public = next(iter(keyset.verifying.values()))
        published = public.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        forged = _forge_hmac(
            {"alg": "HS256", "typ": "JWT", "kid": keyset.signing_kid},
            {
                "iss": settings.issuer,
                "sub": "usr_intruder",
                "aud": settings.audience,
                "iat": int(NOW.timestamp()),
                "exp": int((NOW + timedelta(hours=1)).timestamp()),
            },
            secret=published,
        )

        with pytest.raises(NotAuthenticated):
            tokens.verify(forged, keys=keyset, config=settings, now=NOW)

    def test_a_token_asking_for_no_algorithm_at_all_is_refused(self, keyset, settings):
        unsigned = jwt.encode(
            {
                "iss": settings.issuer,
                "sub": "usr_intruder",
                "aud": settings.audience,
                "iat": int(NOW.timestamp()),
                "exp": int((NOW + timedelta(hours=1)).timestamp()),
            },
            key=None,
            algorithm="none",
        )

        with pytest.raises(NotAuthenticated):
            tokens.verify(unsigned, keys=keyset, config=settings, now=NOW)


class TestClockDrift:
    def test_a_little_disagreement_about_the_time_is_tolerated(
        self, keyset, settings, somebody
    ):
        token, _ = tokens.mint(somebody, keys=keyset, config=settings, now=NOW)
        just_past = NOW + timedelta(seconds=settings.access_ttl_seconds + 10)

        assert tokens.verify(token, keys=keyset, config=settings, now=just_past)

    def test_but_not_an_hour_of_it(self, keyset, settings, somebody):
        token, _ = tokens.mint(somebody, keys=keyset, config=settings, now=NOW)

        with pytest.raises(NotAuthenticated):
            tokens.verify(
                token, keys=keyset, config=settings, now=NOW + timedelta(hours=1)
            )


class TestTheKeysThemselves:
    def test_a_deployment_with_no_private_key_cannot_issue_one(self, monkeypatch):
        monkeypatch.delenv("LUMEN_JWT_PRIVATE_KEY", raising=False)
        monkeypatch.delenv("LUMEN_JWT_PUBLIC_KEYS", raising=False)
        empty = keymod.load(AuthConfig())

        assert empty.can_mint is False
        with pytest.raises(keymod.KeyError_, match="no signing key"):
            empty.require_signing()

    def test_a_verify_only_deployment_can_still_check_tokens(
        self, settings, somebody, monkeypatch
    ):
        # The whole reason for signing asymmetrically: a service can be given
        # the power to check a session without the power to create one.
        private, public = keymod.generate()
        monkeypatch.setenv("LUMEN_JWT_PRIVATE_KEY", private)
        monkeypatch.delenv("LUMEN_JWT_PUBLIC_KEYS", raising=False)
        minting = keymod.load(AuthConfig())
        token, _ = tokens.mint(somebody, keys=minting, config=settings, now=NOW)

        monkeypatch.delenv("LUMEN_JWT_PRIVATE_KEY", raising=False)
        monkeypatch.setenv("LUMEN_JWT_PUBLIC_KEYS", public)
        verifying = keymod.load(AuthConfig())

        assert verifying.can_mint is False
        assert tokens.verify(token, keys=verifying, config=settings, now=NOW).user_id == "usr_1"

    def test_a_key_is_named_by_itself(self, monkeypatch):
        # So the same key has the same name wherever it is configured, and
        # two deployments cannot disagree about which key is which.
        private, public = keymod.generate()
        monkeypatch.setenv("LUMEN_JWT_PRIVATE_KEY", private)
        monkeypatch.setenv("LUMEN_JWT_PUBLIC_KEYS", public)
        loaded = keymod.load(AuthConfig())

        assert len(loaded.verifying) == 1

    def test_the_published_document_carries_only_public_halves(self, keyset):
        document = keyset.jwks()

        assert document["keys"]
        for key in document["keys"]:
            assert key["kty"] == "OKP"
            assert set(key) == {"kty", "crv", "use", "alg", "kid", "x"}
            assert "d" not in key  # the private scalar

    def test_a_key_that_is_not_the_right_kind_is_refused(self, monkeypatch):
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization

        wrong = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = wrong.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode()
        monkeypatch.setenv("LUMEN_JWT_PRIVATE_KEY", pem)

        with pytest.raises(keymod.KeyError_, match="not an Ed25519"):
            keymod.load(AuthConfig())

    def test_a_key_pasted_with_escaped_newlines_still_loads(self, monkeypatch):
        # Which is how a key arrives out of most environment configuration.
        private, _ = keymod.generate()
        monkeypatch.setenv("LUMEN_JWT_PRIVATE_KEY", private.replace("\n", "\\n"))

        assert keymod.load(AuthConfig()).can_mint is True
