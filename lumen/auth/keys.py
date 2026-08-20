"""
The keys that sign a session, and the document that lets somebody check one.

Signing is asymmetric on purpose. The production topology pulls the graph,
query and review services into separate processes, and each of them has to be
able to verify that a token is genuine without holding the power to issue one.
A shared secret cannot do that; a private key here and public keys everywhere
else can.

Several public keys are supported at once, because a rotation is otherwise an
outage: for as long as any unexpired token was signed by the old key, both
halves have to verify.

The private key is read from the environment on every use rather than held as
a setting, for the reason spelled out where the model credentials are read —
settings get snapshotted onto every pipeline run, and this is the one value
that could mint a session for anybody.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from lumen.config import AuthConfig

logger = logging.getLogger(__name__)

# The one algorithm this system signs and verifies with. Pinned in both
# directions: a verifier that accepts whichever algorithm the token's own
# header asks for is how a public key gets used as a signing key.
ALGORITHM = "EdDSA"

# What separates one key from the next when several are configured together.
KEY_SEPARATOR = ","


class KeyError_(RuntimeError):
    """A key is missing or cannot be read."""


@dataclass(frozen=True)
class KeySet:
    """
    Whatever this deployment can do with tokens.

    A deployment with a private key can issue and verify. One with only public
    keys can verify and nothing else, which is exactly what a service that
    reads somebody's graph should be able to do.

    Attributes:
        signing: The private key, when there is one.
        signing_kid: Which key a token minted here says it was signed with.
        verifying: Every public key that might have signed a live token, by
            its identifier.
    """

    signing: Ed25519PrivateKey | None = None
    signing_kid: str = ""
    verifying: dict[str, Ed25519PublicKey] = field(default_factory=dict)

    @property
    def can_mint(self) -> bool:
        """Whether this deployment can issue a session at all."""
        return self.signing is not None

    def require_signing(self) -> Ed25519PrivateKey:
        """
        The signing key, or a refusal that says what to configure.

        Raised rather than returning None because the alternative is an
        unsigned token, and there is no safe thing to do with one.
        """
        if self.signing is None:
            raise KeyError_(
                "no signing key is configured, so this deployment can verify "
                "sessions but not issue them; set LUMEN_JWT_PRIVATE_KEY"
            )
        return self.signing

    def public_key(self, kid: str | None) -> Ed25519PublicKey:
        """
        The key a token says signed it.

        A token naming no key is checked against the only one, when there is
        only one. That keeps a deployment that has never rotated simple,
        without letting a token choose its key where the choice matters.
        """
        if kid:
            found = self.verifying.get(kid)
            if found is None:
                raise KeyError_(f"no public key with id {kid!r}")
            return found
        if len(self.verifying) == 1:
            return next(iter(self.verifying.values()))
        raise KeyError_(
            "the token names no key and several are configured, so there is "
            "no way to tell which was meant"
        )

    def jwks(self) -> dict[str, Any]:
        """
        The public half, in the shape a verifier expects to fetch it in.

        Only ever the public half. This document is served unauthenticated by
        design — it is how something else checks a token without being able
        to make one.
        """
        return {
            "keys": [
                {
                    "kty": "OKP",
                    "crv": "Ed25519",
                    "use": "sig",
                    "alg": ALGORITHM,
                    "kid": kid,
                    "x": _b64(_raw_public(key)),
                }
                for kid, key in sorted(self.verifying.items())
            ]
        }


def load(config: AuthConfig) -> KeySet:
    """
    Read whatever keys this deployment was given.

    A private key implies its own public half, so a deployment that issues
    tokens can always verify the ones it issued without being configured
    twice. Extra public keys are added alongside, which is what makes a
    rotation possible: the new key signs, and the old one still verifies.
    """
    verifying: dict[str, Ed25519PublicKey] = {}

    signing: Ed25519PrivateKey | None = None
    signing_kid = ""
    private = config.jwt_private_key
    if private:
        signing = _read_private(private)
        public = signing.public_key()
        signing_kid = key_id(public)
        verifying[signing_kid] = public

    for pem in _each(config.jwt_public_keys):
        public = _read_public(pem)
        verifying[key_id(public)] = public

    if not verifying:
        logger.info("no signing keys are configured, so sessions cannot be verified")

    return KeySet(signing=signing, signing_kid=signing_kid, verifying=verifying)


def key_id(public: Ed25519PublicKey) -> str:
    """
    What to call a key.

    Derived from the key rather than chosen, so the same key always has the
    same name wherever it is configured, and two deployments describing one
    key cannot disagree about which it is.
    """
    return hashlib.sha256(_raw_public(public)).hexdigest()[:16]


def generate() -> tuple[str, str]:
    """
    A fresh key pair, as the two pieces of text that configure it.

    Here rather than in a script because the tests need pairs constantly, and
    a deployment setting itself up for the first time needs exactly this
    once. Returns (private, public).
    """
    private = Ed25519PrivateKey.generate()
    private_pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


def _read_private(pem: str) -> Ed25519PrivateKey:
    """One private key, refusing anything that is not the right kind."""
    try:
        key = serialization.load_pem_private_key(_bytes(pem), password=None)
    except Exception as exc:  # noqa: BLE001 — every failure here means the same thing
        raise KeyError_(f"the signing key could not be read: {type(exc).__name__}") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise KeyError_("the signing key is not an Ed25519 key")
    return key


def _read_public(pem: str) -> Ed25519PublicKey:
    """One public key, refusing anything that is not the right kind."""
    try:
        key = serialization.load_pem_public_key(_bytes(pem))
    except Exception as exc:  # noqa: BLE001
        raise KeyError_(f"a public key could not be read: {type(exc).__name__}") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise KeyError_("a configured public key is not an Ed25519 key")
    return key


def _each(raw: str | None) -> list[str]:
    """
    Several keys out of one setting.

    Split on the boundary between PEM blocks rather than on a separator, so a
    key can be pasted in with its newlines intact — which is how anybody
    actually has one.
    """
    if not raw:
        return []
    text = _bytes(raw).decode()
    blocks = [
        block.strip() for block in text.split("-----END PUBLIC KEY-----") if block.strip()
    ]
    return [f"{block}\n-----END PUBLIC KEY-----\n" for block in blocks]


def _bytes(pem: str) -> bytes:
    """
    A key as bytes, tolerating the way environments mangle newlines.

    A PEM block pasted into an environment variable usually arrives with its
    line breaks written as two characters. Refusing that would be correct and
    would cost somebody an hour finding out why.
    """
    return pem.replace("\\n", "\n").strip().encode()


def _raw_public(key: Ed25519PublicKey) -> bytes:
    """The bare 32 bytes of a public key."""
    return key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def _b64(raw: bytes) -> str:
    """Bytes as the padding-free base64 a key document uses."""
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


__all__ = ["ALGORITHM", "KeySet", "KeyError_", "load", "key_id", "generate"]
