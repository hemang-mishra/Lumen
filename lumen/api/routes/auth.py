"""
Signing in, staying signed in, and signing out.

The one part of the API reachable by somebody who has proved nothing, which
is why it is also the only part with a rate limit.

Three things about the shapes here are security decisions rather than style.

**The renewable half of a session is a cookie the browser cannot read**, and
the short-lived half is in the body so it can be held in memory. Putting
either in storage a script can reach would mean that any script which gets
onto the page can take somebody's history with it.

**That cookie is scoped to this path.** It is sent when renewing a session and
at no other time, so the credential that outlives everything else in the
system is not attached to every request that happens to go to this host.

**The state is compared, not merely present.** It is set as a cookie when a
sign-in starts and sent back in the body when it finishes; matching the two is
what proves the person who came back is the person who left. Without it,
somebody else can complete a sign-in inside this browser's session.
"""

from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Depends, Request, Response, status

from lumen.api.deps import get_auth, get_config, get_eraser, require_identity
from lumen.api.errors import BadRequest, NotFound
from lumen.api.schemas import (
    SessionView,
    SignedOutView,
    SignInCallbackRequest,
    SignInStartView,
    UserView,
)
from lumen.auth import AuthService, Identity
from lumen.auth.contracts import (
    NotAuthenticated,
    SignUpRefused,
    TokenPair,
    TooManyAttempts,
)
from lumen.config import AppConfig

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# The three cookies this flow uses. The first outlives a session; the other
# two live only for the seconds somebody is away at the provider.
REFRESH_COOKIE = "lumen_refresh"
STATE_COOKIE = "lumen_state"
VERIFIER_COOKIE = "lumen_verifier"

# Where the renewable cookie is sent. Deliberately narrow.
REFRESH_PATH = "/auth"

# How long the two in-flight cookies are worth keeping. Long enough to sign
# in unhurriedly, short enough that an abandoned attempt does not linger.
FLOW_SECONDS = 600


@router.get("/google/start", response_model=SignInStartView)
def begin_signing_in(
    response: Response,
    request: Request,
    auth: AuthService = Depends(get_auth),
    config: AppConfig = Depends(get_config),
) -> SignInStartView:
    """
    Where to send somebody to prove who they are.

    The two secrets that go with the URL are set as cookies the browser
    cannot read and are never returned in the body — handing them to a script
    would defeat the only thing they are for.
    """
    try:
        start = auth.start(caller=_caller(request))
    except TooManyAttempts as slow:
        raise _too_many(slow) from slow

    _set_flow_cookie(response, STATE_COOKIE, start.state, config)
    _set_flow_cookie(response, VERIFIER_COOKIE, start.verifier, config)
    return SignInStartView(authorization_url=start.authorization_url)


@router.post("/google/callback", response_model=SessionView)
def finish_signing_in(
    body: SignInCallbackRequest,
    request: Request,
    response: Response,
    auth: AuthService = Depends(get_auth),
    config: AppConfig = Depends(get_config),
) -> SessionView:
    """
    Accept what somebody came back with, and start their session.

    The state is checked first and compared in constant time, before anything
    is exchanged. Nothing about the code is looked at until this request has
    been shown to belong to the flow that started it.
    """
    expected = request.cookies.get(STATE_COOKIE) or ""
    verifier = request.cookies.get(VERIFIER_COOKIE) or ""

    if not expected or not secrets.compare_digest(expected, body.state):
        logger.warning("a sign-in came back without the flow it left on")
        raise BadRequest("this sign-in did not match the one that was started")

    try:
        identity, session = auth.complete(
            body.code,
            verifier,
            agent=request.headers.get("user-agent"),
            ip=_caller(request),
        )
    except TooManyAttempts as slow:
        raise _too_many(slow) from slow
    except SignUpRefused as refused:
        raise _forbidden(refused) from refused
    except NotAuthenticated as refused:
        raise _unauthorised(refused.reason) from refused

    _clear(response, STATE_COOKIE, config)
    _clear(response, VERIFIER_COOKIE, config)
    _set_refresh_cookie(response, session, config)

    return _session_view(auth, identity, session)


@router.post("/refresh", response_model=SessionView)
def keep_the_session(
    request: Request,
    response: Response,
    auth: AuthService = Depends(get_auth),
    config: AppConfig = Depends(get_config),
) -> SessionView:
    """
    Exchange the renewable half of a session for a fresh one.

    The only endpoint that reads that cookie. A token presented twice ends
    every session in its chain before this answers, so the reply is the same
    refusal whether it was theft or a race.
    """
    presented = request.cookies.get(REFRESH_COOKIE) or ""
    if not presented:
        raise _unauthorised("there is no session to renew")

    try:
        session = auth.refresh(
            presented,
            agent=request.headers.get("user-agent"),
            ip=_caller(request),
        )
    except NotAuthenticated as refused:
        _clear(response, REFRESH_COOKIE, config, path=REFRESH_PATH)
        raise _unauthorised(refused.reason) from refused

    _set_refresh_cookie(response, session, config)
    identity = auth.identify(session.access_token)
    return _session_view(auth, identity, session)


@router.post("/logout", response_model=SignedOutView)
def sign_out(
    request: Request,
    response: Response,
    auth: AuthService = Depends(get_auth),
    config: AppConfig = Depends(get_config),
) -> SignedOutView:
    """
    End this session.

    This one, not every one. Signing out on a shared computer means this
    device; ending somebody's phone session too would be surprising, and
    losing a device is the different request.

    Answers the same way whether or not there was a session, because a
    caller with nothing to sign out of is already in the state they asked
    for.
    """
    presented = request.cookies.get(REFRESH_COOKIE)
    if presented:
        auth.sign_out(presented)
    _clear(response, REFRESH_COOKIE, config, path=REFRESH_PATH)
    return SignedOutView()


@router.get("/me", response_model=UserView)
def who_am_i(
    identity: Identity = Depends(require_identity),
    auth: AuthService = Depends(get_auth),
) -> UserView:
    """
    The person this session belongs to.

    What the browser asks on load to find out whether it is signed in.
    Everything here can change without waiting for a token to expire, which
    is exactly why none of it travels in one.
    """
    return _user_view(auth, identity)


@router.delete("/me", response_model=SignedOutView)
def forget_me(
    request: Request,
    response: Response,
    identity: Identity = Depends(require_identity),
    auth: AuthService = Depends(get_auth),
    config: AppConfig = Depends(get_config),
    eraser=Depends(get_eraser),
) -> SignedOutView:
    """
    Ask to be forgotten.

    Sessions end first and the data goes second, in that order — erasing
    while a session is live means requests arriving for history that is
    disappearing underneath them.

    The erasure itself is the one that already exists, so this is a route
    into it rather than a second way of deleting things.
    """
    from lumen.erasure.contracts import ErasureRequest
    from lumen.operational.enums import ErasureScope

    ended = auth.end_every_session(identity.user_id)
    _clear(response, REFRESH_COOKIE, config, path=REFRESH_PATH)

    auth.begin_erasure(identity.user_id)
    eraser.erase(
        ErasureRequest(
            user_id=identity.user_id,
            scope=ErasureScope.ALL,
            confirmation=config.maintenance.erasure_confirm_phrase,
        )
    )
    return SignedOutView(sessions_ended=ended)


@router.get("/.well-known/jwks.json")
def public_keys(auth: AuthService = Depends(get_auth)) -> dict:
    """
    The public half of the signing keys.

    Served to anybody, which is the point: it is how something that verifies
    a token proves it is genuine without being able to make one.
    """
    return auth.jwks


# ---------------------------------------------------------------------------
# Cookies, callers, and refusals
# ---------------------------------------------------------------------------


def _set_refresh_cookie(response: Response, session: TokenPair, config: AppConfig) -> None:
    """
    Put the renewable half of a session where a script cannot reach it.

    Scoped to the sign-in path, so the longest-lived credential in the system
    is attached only to the request that renews it.
    """
    settings = config.auth
    response.set_cookie(
        REFRESH_COOKIE,
        session.refresh_token,
        max_age=settings.refresh_ttl_seconds,
        path=REFRESH_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
    )


def _set_flow_cookie(response: Response, name: str, value: str, config: AppConfig) -> None:
    """One of the two short-lived cookies that hold a sign-in together."""
    settings = config.auth
    response.set_cookie(
        name,
        value,
        max_age=FLOW_SECONDS,
        path="/",
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
    )


def _clear(response: Response, name: str, config: AppConfig, *, path: str = "/") -> None:
    """Remove a cookie, with the same flags it was set with."""
    settings = config.auth
    response.delete_cookie(
        name,
        path=path,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
    )


def _caller(request: Request) -> str | None:
    """
    Who is asking, for the purpose of counting attempts.

    Read from the connection rather than from a header. A forwarded-for
    header is whatever the client wrote in it, and a rate limit keyed on
    something the caller chooses is not a rate limit.
    """
    return request.client.host if request.client else None


def _session_view(auth: AuthService, identity: Identity, session: TokenPair) -> SessionView:
    """A new session, in the shape the browser is given it."""
    return SessionView(
        access_token=session.access_token,
        expires_in=session.expires_in,
        user=_user_view(auth, identity),
    )


def _user_view(auth: AuthService, identity: Identity) -> UserView:
    """Somebody as they are shown to themselves."""
    stored = auth.describe(identity.user_id)
    if stored is None:
        raise NotFound("user", identity.user_id)
    return UserView(
        user_id=stored.user_id,
        email=stored.email,
        display_name=stored.display_name,
        avatar_url=stored.avatar_url,
        status=stored.status.value,
        created_at=stored.created_at,
        last_seen_at=stored.last_seen_at,
    )


def _unauthorised(reason: str) -> Exception:
    """
    A refusal that says what is wrong with the credential.

    Never whether the person it names exists — that would make a sign-in page
    a way of finding out which addresses are worth trying.
    """
    from fastapi import HTTPException

    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=reason,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _forbidden(refused: SignUpRefused) -> Exception:
    """Proved who they are, and the answer is still no."""
    from fastapi import HTTPException

    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(refused))


def _too_many(slow: TooManyAttempts) -> Exception:
    """Trying faster than a person tries."""
    from fastapi import HTTPException

    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(slow)
    )


__all__ = ["router", "REFRESH_COOKIE", "STATE_COOKIE", "VERIFIER_COOKIE"]
