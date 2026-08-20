"""
A stand-in for Google, so the sign-in flow can be tested without a network.

A real key, a real signature, and a real verification — only the transport is
replaced. Mocking the verification instead would test that a mock returns what
it was told to, which is the one thing about sign-in nobody needs to know.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa


class FakeGoogleKeys:
    """Google's published keys, except local."""

    def __init__(self, key) -> None:
        self._key = key

    def get_signing_key_from_jwt(self, token: str):
        class Signing:
            key = self._key.public_key()

        return Signing()


class FakeTokenEndpoint:
    """Google's token endpoint, except it just hands back what it was given."""

    def __init__(self, id_token: str | None, *, status: int = 200) -> None:
        self._id_token = id_token
        self._status = status
        self.calls: list[dict] = []

    def post(self, url, data=None):
        self.calls.append(dict(data or {}))
        endpoint = self

        class Answer:
            status_code = endpoint._status

            @staticmethod
            def json():
                return {"id_token": endpoint._id_token} if endpoint._id_token else {}

        return Answer()

    def close(self) -> None:
        pass


def google_key():
    """One RSA key, as Google would sign with."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def id_token(
    key,
    *,
    client_id: str = "client-1",
    subject: str = "google-sub-1",
    email: str = "person@example.com",
    audience: str | None = None,
    issuer: str = "https://accounts.google.com",
    email_verified: bool = True,
    expired: bool = False,
    name: str = "A Person",
) -> str:
    """One assertion from Google, correct or wrong in a chosen way."""
    now = datetime.now(UTC)
    ends = now - timedelta(hours=1) if expired else now + timedelta(hours=1)
    return jwt.encode(
        {
            "iss": issuer,
            "sub": subject,
            "aud": audience or client_id,
            "iat": int((now - timedelta(minutes=1)).timestamp()),
            "exp": int(ends.timestamp()),
            "email": email,
            "email_verified": email_verified,
            "name": name,
            "picture": "https://example.com/a.png",
        },
        key,
        algorithm="RS256",
    )
