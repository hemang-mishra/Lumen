"""
The narrow way in to identity.

Everything above this — the web layer, and later a command line — talks to
this and nothing else. What it gets is the four things sign-in actually is:
send somebody to prove who they are, accept what they come back with, renew a
session, and end one.

Three rules live here rather than deeper down, because all three are about
whether somebody may have a session at all rather than about how one is made.

**Who is allowed an account.** By default a named list, because an open
sign-in on a reachable host hands a database, a search index and a model
budget to whoever finds the port.

**Signing in is slowed down.** It is the only door that opens to somebody who
has proved nothing yet.

**A refusal never says whether an account exists.** "That address is not on
the list" and "no such user" are the same answer to whoever is asking, because
the difference between them turns a sign-in page into a way of finding out
which addresses are worth trying.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from lumen.auth.contracts import (
    ExternalIdentity,
    Identity,
    NotAuthenticated,
    SignInStart,
    SignUpRefused,
    TokenPair,
    TooManyAttempts,
)
from lumen.auth.keys import KeySet
from lumen.auth.limits import SignInLimiter
from lumen.auth.provider import IdentityProvider
from lumen.auth.repository import IdentityRepository
from lumen.auth.sessions import SessionService
from lumen.auth import tokens
from lumen.config import AuthConfig
from lumen.operational.enums import AuthProvider, UserStatus
from lumen.operational.schemas import UserRecord

logger = logging.getLogger(__name__)

# Sign-up modes, in the order of how much they trust a stranger.
OPEN = "open"
ALLOWLIST = "allowlist"


class AuthService:
    """
    Sign-in, renewal, sign-out, and deciding whether a token is still good.

    Holds the pieces and composes them; every one of them is testable without
    this, and this is testable with stand-ins for all of them.
    """

    def __init__(
        self,
        *,
        repository: IdentityRepository,
        provider: IdentityProvider,
        keys: KeySet,
        config: AuthConfig,
        limiter: SignInLimiter | None = None,
    ) -> None:
        self._repository = repository
        self._provider = provider
        self._keys = keys
        self._config = config
        self._limiter = limiter or SignInLimiter(config)
        self._sessions = SessionService(repository, keys=keys, config=config)

    # ------------------------------------------------------------------
    # Signing in
    # ------------------------------------------------------------------

    def start(self, *, caller: str | None = None, now: datetime | None = None) -> SignInStart:
        """
        Begin a sign-in, if this caller has not been trying too often.

        Raises:
            TooManyAttempts: Slow down.
        """
        if not self._limiter.allows(caller, now=now or _now()):
            raise TooManyAttempts("too many sign-in attempts; wait a few minutes")
        return self._provider.start()

    def complete(
        self,
        code: str,
        verifier: str,
        *,
        agent: str | None = None,
        ip: str | None = None,
        now: datetime | None = None,
    ) -> tuple[Identity, TokenPair]:
        """
        Accept what somebody came back with, and give them a session.

        Raises:
            NotAuthenticated: What they came back with does not prove anything.
            SignUpRefused: It proves who they are, and the answer is still no.
            TooManyAttempts: Slow down.
        """
        moment = now or _now()
        external = self._provider.verify(code, verifier)

        if not self._limiter.allows(ip, external.email.lower(), now=moment):
            raise TooManyAttempts("too many sign-in attempts; wait a few minutes")

        user = self._resolve(external, now=moment)
        self._limiter.forget(ip, external.email.lower())

        identity = _as_identity(user)
        return identity, self._sessions.issue(identity, agent=agent, ip=ip, now=moment)

    def _resolve(self, external: ExternalIdentity, *, now: datetime) -> UserRecord:
        """
        Find who this is, or make them, or refuse.

        Three routes in, and the order is deliberate. A linked account is the
        ordinary case. An address that already belongs to somebody is the
        same person arriving from a second provider, and linking is better
        than a duplicate account holding half their history. Everything else
        is new, and new is where the allowlist applies.
        """
        provider = AuthProvider(external.provider)

        user = self._repository.find_by_identity(provider, external.subject)
        if user is None:
            user = self._repository.find_by_email(external.email)
            if user is not None:
                self._repository.link_identity(
                    user.user_id,
                    provider=provider,
                    subject=external.subject,
                    email=external.email,
                )
                logger.info(
                    "a second way of signing in was linked to an existing person",
                    extra={"user_id": user.user_id},
                )

        if user is None:
            user = self._create(external)

        if not user.active:
            # Suspended or being erased. Not a sign-in problem, and saying
            # which would be telling a stranger about somebody's account.
            raise NotAuthenticated("this account is not active")

        self._repository.touch(
            user.user_id, at=now, display_name=external.display_name
        )
        return user

    def _create(self, external: ExternalIdentity) -> UserRecord:
        """Make a person, if this deployment lets strangers in at all."""
        if not self._may_sign_up(external.email):
            logger.warning("somebody not on the list tried to sign up")
            raise SignUpRefused("this address cannot sign in here")

        user = self._repository.create_user(
            email=external.email,
            display_name=external.display_name,
            avatar_url=external.avatar_url or None,
        )
        self._repository.link_identity(
            user.user_id,
            provider=AuthProvider(external.provider),
            subject=external.subject,
            email=external.email,
        )
        return user

    def _may_sign_up(self, email: str) -> bool:
        """
        Whether a new person is allowed here.

        Anything other than an explicit "open" is treated as the list, so a
        misspelled setting is restrictive rather than permissive — which is
        the direction a mistake should fail in when the cost is a stranger
        getting a database.
        """
        if self._config.signup_mode.strip().lower() == OPEN:
            return True
        return email.strip().lower() in self._config.allowlist

    # ------------------------------------------------------------------
    # Keeping and ending a session
    # ------------------------------------------------------------------

    def refresh(
        self,
        presented: str,
        *,
        agent: str | None = None,
        ip: str | None = None,
        now: datetime | None = None,
    ) -> TokenPair:
        """Exchange a refresh token for a new session."""
        return self._sessions.rotate(presented, agent=agent, ip=ip, now=now)

    def sign_out(self, presented: str, *, now: datetime | None = None) -> None:
        """End this session. Not the person's other ones."""
        self._sessions.revoke(presented, now=now)

    def end_every_session(self, user_id: str, *, now: datetime | None = None) -> int:
        """End every session this person holds, everywhere."""
        return self._sessions.revoke_everything(user_id, now=now)

    def begin_erasure(self, user_id: str, *, now: datetime | None = None) -> None:
        """
        Mark somebody as leaving, and end their sessions.

        In that order, and this is the whole reason it is one method: erasing
        while a session is still live means requests arriving for history that
        is disappearing underneath them.
        """
        self._repository.set_status(user_id, UserStatus.ERASURE_PENDING)
        self.end_every_session(user_id, now=now)
        logger.warning("a person asked to be forgotten", extra={"user_id": user_id})

    # ------------------------------------------------------------------
    # Deciding whether a token is still good
    # ------------------------------------------------------------------

    def identify(self, token: str, *, now: datetime | None = None) -> Identity:
        """
        Who a token says this is, if it still says anything.

        Two checks, and the second is the one a signature cannot make. The
        token proves it was issued by us and has not expired; only the store
        can say whether the session it belongs to has since been ended, and
        that is what the generation number is compared for.

        Raises:
            NotAuthenticated: With a short reason a person can act on.
        """
        claims = tokens.verify(
            token, keys=self._keys, config=self._config, now=now
        )

        user = self._repository.find_user(claims.user_id)
        if user is None:
            # The token is genuine and names nobody. Answered as an ordinary
            # refusal rather than as "no such user", which would make a
            # stolen token a way of asking who exists.
            raise NotAuthenticated("this session is no longer valid")

        if claims.token_version != user.token_version:
            raise NotAuthenticated("this session was ended; sign in again")

        if not user.active:
            raise NotAuthenticated("this account is not active")

        return _as_identity(user)

    def describe(self, user_id: str) -> UserRecord | None:
        """The person behind an identity, for the endpoint that shows them."""
        return self._repository.find_user(user_id)

    @property
    def jwks(self) -> dict:
        """The public keys, for anything that verifies without minting."""
        return self._keys.jwks()


def _as_identity(user: UserRecord) -> Identity:
    """A stored person as the identity a request carries."""
    return Identity(
        user_id=user.user_id,
        email=user.email,
        display_name=user.display_name,
        token_version=user.token_version,
        authenticated=True,
    )


def _now() -> datetime:
    """The moment something is being asked at."""
    return datetime.now(UTC)


__all__ = ["AuthService", "OPEN", "ALLOWLIST"]
