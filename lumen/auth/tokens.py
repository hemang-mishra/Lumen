"""
Making a session token, and deciding whether one is genuine.

No store, no clock of its own, no vendor. This module turns an identity into
a signed string and back again, and everything it needs to decide is either
in the token or handed in — which is what lets every rule below be tested with
nothing running.

Three things are checked that are easy to leave out, and each has cost
somebody a breach:

**The algorithm is pinned in both directions.** A verifier that accepts
whichever algorithm the token's own header names can be handed a token saying
"signed with HMAC, using the public key as the secret" — and the public key is
public. Only EdDSA is offered and only EdDSA is accepted.

**The audience and the issuer are verified, not read.** A token minted by a
system that shares our signing key, for something that is not this API, is a
valid signature over the wrong claim.

**Nothing here decides whether a session is still live.** The generation
number travels in the token and is compared against what is stored by the
layer that has a database. Keeping that comparison out of here is what keeps
this module free of one.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

import jwt

from lumen.auth.contracts import AccessClaims, Identity, NotAuthenticated
from lumen.auth.keys import ALGORITHM, KeySet
from lumen.config import AuthConfig

logger = logging.getLogger(__name__)

# The claim carrying which generation of session a token belongs to. Short
# because it travels on every request.
VERSION_CLAIM = "tv"

# A little slack for two machines that disagree about the time. Small enough
# that it cannot meaningfully extend a token, large enough to absorb the
# ordinary drift between a laptop and a server.
LEEWAY_SECONDS = 30


def mint(
    identity: Identity,
    *,
    keys: KeySet,
    config: AuthConfig,
    now: datetime | None = None,
) -> tuple[str, int]:
    """
    Sign a token saying who this is, and for how long.

    Returns the token and how many seconds it is good for, because the caller
    has to tell the browser when to come back and should not have to work it
    out from the token it was just handed.
    """
    moment = now or _now()
    lifetime = max(int(config.access_ttl_seconds), 1)
    expires = moment + timedelta(seconds=lifetime)

    token = jwt.encode(
        {
            "iss": config.issuer,
            "sub": identity.user_id,
            "aud": config.audience,
            "iat": int(moment.timestamp()),
            "exp": int(expires.timestamp()),
            "jti": uuid.uuid4().hex,
            "email": identity.email,
            VERSION_CLAIM: identity.token_version,
        },
        keys.require_signing(),
        algorithm=ALGORITHM,
        headers={"kid": keys.signing_kid} if keys.signing_kid else None,
    )
    return token, lifetime


def verify(
    token: str,
    *,
    keys: KeySet,
    config: AuthConfig,
    now: datetime | None = None,
) -> AccessClaims:
    """
    Decide whether a token is genuine, and say what it claims.

    Every failure comes back as the same kind of refusal carrying a different
    short reason. The reason is safe to repeat to whoever asked — it says
    what is wrong with the token, never anything about whether the person it
    names exists.

    Raises:
        NotAuthenticated: The token is missing, malformed, expired, signed by
            something else, or meant for somebody else.
    """
    if not token:
        raise NotAuthenticated("no token was presented")

    try:
        public = keys.public_key(_kid(token))
    except Exception as exc:  # noqa: BLE001 — an unreadable header is a bad token
        logger.debug("a token named a key nothing here holds")
        raise NotAuthenticated("this token was signed by something unknown") from exc

    try:
        claims = jwt.decode(
            token,
            public,
            # Pinned. Never read from the token's own header.
            algorithms=[ALGORITHM],
            audience=config.audience,
            issuer=config.issuer,
            leeway=LEEWAY_SECONDS,
            options={
                "require": ["exp", "iat", "sub", "aud", "iss"],
                # The library checks the time against the real clock, which
                # is right in production and impossible to test against. When
                # a caller names the moment, the same checks are made below
                # against that moment instead. Never neither: `_check_time`
                # is called on exactly the branch this turns off.
                "verify_exp": now is None,
                "verify_iat": now is None,
            },
        )
    except jwt.ExpiredSignatureError as exc:
        raise NotAuthenticated("this session has expired") from exc
    except jwt.InvalidAudienceError as exc:
        raise NotAuthenticated("this token was issued for something else") from exc
    except jwt.InvalidIssuerError as exc:
        raise NotAuthenticated("this token was issued by something else") from exc
    except jwt.InvalidSignatureError as exc:
        raise NotAuthenticated("this token's signature does not match") from exc
    except jwt.InvalidTokenError as exc:
        # Everything else the library distinguishes collapses here on
        # purpose. A malformed token is malformed; enumerating the ways
        # would tell whoever sent it how to send a better one.
        raise NotAuthenticated("this token could not be read") from exc

    subject = str(claims.get("sub") or "")
    if not subject:
        raise NotAuthenticated("this token names nobody")

    expires = datetime.fromtimestamp(int(claims["exp"]), tz=UTC)
    issued = datetime.fromtimestamp(int(claims["iat"]), tz=UTC)
    _check_time(issued, expires, now)

    return AccessClaims(
        user_id=subject,
        email=str(claims.get("email") or ""),
        token_version=int(claims.get(VERSION_CLAIM) or 0),
        token_id=str(claims.get("jti") or ""),
        expires_at=expires,
    )


def _check_time(issued: datetime, expires: datetime, now: datetime | None) -> None:
    """
    Whether a token is inside its own lifetime, at a moment the caller named.

    Only runs when a caller supplies the moment; otherwise the library has
    already made exactly these checks against the real clock. The two paths
    apply the same two rules with the same tolerance, so a token accepted by
    one would be accepted by the other.

    It exists because everything else in this system takes its clock as an
    argument, and a rule that can only be exercised by waiting fifteen
    minutes is a rule nobody ever exercises.
    """
    if now is None:
        return

    slack = timedelta(seconds=LEEWAY_SECONDS)
    if now > expires + slack:
        raise NotAuthenticated("this session has expired")
    if issued - slack > now:
        # Dated in the future. Either two clocks disagree by more than the
        # tolerance, or somebody is trying to extend a token's life by
        # backdating when it starts.
        raise NotAuthenticated("this token is not valid yet")


def _kid(token: str) -> str | None:
    """
    Which key a token says signed it, if it says.

    Read from the unverified header, which is safe for exactly this: choosing
    which public key to check the signature against. Nothing else in the
    header is trusted, and the algorithm in particular is ignored.
    """
    try:
        return jwt.get_unverified_header(token).get("kid")
    except jwt.InvalidTokenError:
        return None


def _now() -> datetime:
    """The moment a token is being minted or checked at."""
    return datetime.now(UTC)


__all__ = ["mint", "verify", "VERSION_CLAIM", "LEEWAY_SECONDS"]
