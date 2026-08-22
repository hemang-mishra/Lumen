"""
Handing out sessions, exchanging them, and ending them.

A session is a long-lived secret, which makes it the most dangerous thing this
system gives anybody. Three rules contain that.

**The token is never stored.** What is written down is a hash, so somebody who
reads the database learns who has a session and cannot use one.

**It is exchanged every time it is used.** Each exchange issues a new token and
records on the old one what replaced it, so a token has exactly one legitimate
use.

**A second use ends everything.** A token arriving after it has been exchanged
is either stolen or a race, and both get the same answer: every session in the
chain is ended and the person signs in again. This is the rule that turns a
stolen token from a permanent compromise into an event somebody finds out
about — without it, a thief holds a renewable session for thirty days and
nobody ever learns.

The one thing deliberately absent is a way to look at a token. Nothing here
returns a stored secret, because nothing stores one.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from lumen.auth.contracts import (
    Identity,
    NotAuthenticated,
    TokenPair,
    TokenReused,
)
from lumen.auth.keys import KeySet
from lumen.auth.repository import IdentityRepository
from lumen.operational.schemas import hash_ip, hash_token
from lumen.auth import tokens
from lumen.config import AuthConfig

logger = logging.getLogger(__name__)

# How much randomness a refresh token carries. 256 bits, which is not a
# number to be economical about: this is the credential that outlives every
# other one in the system.
TOKEN_BYTES = 32


class SessionService:
    """
    Issues and exchanges the long-lived half of a session.

    Holds the store and the keys and nothing else. What a caller can do with
    one of these is start a session, exchange one, or end one — there is no
    method that hands back a stored secret, because none is stored.
    """

    def __init__(
        self,
        repository: IdentityRepository,
        *,
        keys: KeySet,
        config: AuthConfig,
    ) -> None:
        self._repository = repository
        self._keys = keys
        self._config = config

    def issue(
        self,
        identity: Identity,
        *,
        agent: str | None = None,
        ip: str | None = None,
        now: datetime | None = None,
    ) -> TokenPair:
        """
        Start a session: a short token to use, and a long one to renew with.

        The long one is returned here and nowhere else, ever again.
        """
        moment = now or _now()
        access, lifetime = tokens.mint(
            identity, keys=self._keys, config=self._config, now=moment
        )
        refresh, expires = self._store_refresh(
            identity.user_id, agent=agent, ip=ip, now=moment
        )

        logger.info("a session was started", extra={"user_id": identity.user_id})
        return TokenPair(
            access_token=access,
            expires_in=lifetime,
            refresh_token=refresh,
            refresh_expires_at=expires,
        )

    def rotate(
        self,
        presented: str,
        *,
        agent: str | None = None,
        ip: str | None = None,
        now: datetime | None = None,
    ) -> TokenPair:
        """
        Exchange a refresh token for a new session.

        Raises:
            TokenReused: This token has already been exchanged. The whole
                chain is ended before this is raised, so by the time a caller
                sees it there is nothing left to steal.
            NotAuthenticated: The token is unknown, expired, or already ended.
        """
        moment = now or _now()
        stored = self._repository.find_session(hash_token(presented))
        if stored is None:
            raise NotAuthenticated("this session is not recognised")

        if stored.rotated_to is not None:
            # Presented twice. Either somebody else has it, or this client
            # retried — and there is no way to tell the two apart, so both
            # are answered as theft.
            self._repository.revoke_chain(stored.token_id, at=moment)
            self._repository.bump_token_version(stored.user_id)
            logger.warning(
                "a session token was presented after it had been exchanged, "
                "so every session in the chain was ended",
                extra={"user_id": stored.user_id},
            )
            raise TokenReused("this session was already renewed; sign in again")

        if not stored.usable_at(moment):
            raise NotAuthenticated("this session has ended")

        user = self._repository.find_user(stored.user_id)
        if user is None or not user.active:
            raise NotAuthenticated("this account is not active")

        refresh, expires = self._store_refresh(
            user.user_id, agent=agent, ip=ip, now=moment
        )
        self._repository.mark_rotated(stored.token_id, replacement=self._last_id)

        access, lifetime = tokens.mint(
            Identity(
                user_id=user.user_id,
                email=user.email,
                display_name=user.display_name,
                token_version=user.token_version,
            ),
            keys=self._keys,
            config=self._config,
            now=moment,
        )
        return TokenPair(
            access_token=access,
            expires_in=lifetime,
            refresh_token=refresh,
            refresh_expires_at=expires,
        )

    def revoke(self, presented: str, *, now: datetime | None = None) -> None:
        """
        End the session this token belongs to, and only that one.

        Signing out on a shared computer means this device. Ending somebody's
        phone session too would be surprising, and losing a device is the
        different request.

        A token nobody recognises is not an error: the session is already in
        the state being asked for.
        """
        stored = self._repository.find_session(hash_token(presented))
        if stored is None:
            return
        self._repository.revoke_session(stored.token_id, at=now or _now())
        logger.info("a session was ended", extra={"user_id": stored.user_id})

    def revoke_everything(self, user_id: str, *, now: datetime | None = None) -> int:
        """
        End every session this person holds, everywhere.

        Both halves matter. The stored sessions are ended so none can be
        exchanged again, and the generation number is bumped so the short
        tokens already out there stop verifying — without which somebody
        would keep working for up to fifteen minutes after being locked out.
        """
        moment = now or _now()
        ended = self._repository.revoke_all_sessions(user_id, at=moment)
        self._repository.bump_token_version(user_id)
        return ended

    def _store_refresh(
        self,
        user_id: str,
        *,
        agent: str | None,
        ip: str | None,
        now: datetime,
    ) -> tuple[str, datetime]:
        """
        Make a refresh token, write down its hash, and hand back the token.

        The only place a usable session secret exists is the return value of
        this function and the reply it goes into.
        """
        token = secrets.token_urlsafe(TOKEN_BYTES)
        token_id = uuid.uuid4().hex
        expires = now + timedelta(seconds=max(self._config.refresh_ttl_seconds, 1))

        self._repository.save_session(
            token_id=token_id,
            user_id=user_id,
            token_hash=hash_token(token),
            issued_at=now,
            expires_at=expires,
            user_agent=(agent or "")[:512] or None,
            ip_hash=hash_ip(ip),
        )
        self._last_id = token_id
        return token, expires


def _now() -> datetime:
    """The moment a session is being issued or exchanged at."""
    return datetime.now(UTC)


__all__ = ["SessionService", "TOKEN_BYTES"]
