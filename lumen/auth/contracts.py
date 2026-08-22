"""
The shapes identity travels in.

`Identity` is the one that matters. Every route that touches somebody's data
takes one, rather than reading configuration, and that difference is the whole
of this goal: a route that reads configuration serves whoever the process was
started as, which in a system with two users is a data leak that looks exactly
like working software.

The errors are separated by what a caller can do about them. "Your session
expired" and "that token was meant for something else" are both refusals, and
only one of them is fixed by signing in again.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class Identity(BaseModel):
    """
    Who is asking.

    Attributes:
        user_id: The identifier everything of theirs is keyed by. Generated
            and permanent — never their email, which people change, and never
            the identity provider's, which outlives nothing.
        email: Their address, as the provider verified it.
        display_name: What to call them.
        token_version: Which generation of session this is. A mismatch with
            what is stored is how every outstanding session is ended at once,
            without a list of revoked tokens to consult.
        authenticated: False for the configured offline default — the command
            line, a background job, a deployment with sign-in switched off.
            Kept so a caller can tell a real person from a fallback without
            reading configuration itself.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1)
    email: str = ""
    display_name: str = ""
    token_version: int = Field(default=0, ge=0)
    authenticated: bool = True


class AccessClaims(BaseModel):
    """
    What a verified access token says.

    Deliberately thin. A token is not a profile: anything richer changes
    without waiting for one to expire, and belongs to the endpoint that can
    answer it fresh.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1)
    email: str = ""
    token_version: int = Field(default=0, ge=0)
    token_id: str = ""
    expires_at: datetime


class TokenPair(BaseModel):
    """
    A new session.

    The refresh half is returned exactly once, here, and never stored in a
    form that could be used — what is kept is a hash of it, so reading the
    database cannot produce a working session.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    access_token: str = Field(min_length=1)
    expires_in: int = Field(gt=0)
    refresh_token: str = Field(min_length=1)
    refresh_expires_at: datetime


class ExternalIdentity(BaseModel):
    """
    Somebody as an identity provider vouches for them.

    The shape every provider is reduced to before anything else sees it, so
    that adding a second one is a new module rather than a change to how
    users work.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    email: str = Field(min_length=1)
    email_verified: bool = False
    display_name: str = ""
    avatar_url: str = ""


class SignInStart(BaseModel):
    """Where to send somebody to sign in, and what to remember while they are gone."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    authorization_url: str = Field(min_length=1)
    state: str = Field(min_length=1)
    verifier: str = Field(min_length=1)


class AuthError(Exception):
    """Something about who is asking is wrong."""


class NotAuthenticated(AuthError):
    """
    No usable proof of who this is.

    Carries a short reason a person can act on — expired, wrong signature,
    session ended — because "unauthorized" tells somebody nothing about
    whether to sign in again or to call somebody. The reason never says
    whether an account exists: that would turn a sign-in page into a way of
    finding out which addresses are worth trying.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class SignUpRefused(AuthError):
    """
    A real person, correctly signed in, who is not allowed an account here.

    Kept apart from a failed sign-in because nothing went wrong: they proved
    who they are and the answer is still no.
    """


class TokenReused(NotAuthenticated):
    """
    A refresh token was presented after it had already been exchanged.

    Either theft or a race, and the answer is the same for both: the whole
    chain is revoked before this is raised. This is the mechanism that turns
    a stolen refresh token from a permanent compromise into an event
    somebody finds out about.

    A kind of `NotAuthenticated` rather than a sibling of it, deliberately.
    Every caller that already handles a refused credential handles this one
    correctly by doing nothing; a separate branch would be one somebody has
    to remember to write, and forgetting it turns the most security-relevant
    event in the system into an unhandled error.
    """


class TooManyAttempts(AuthError):
    """Sign-in is being tried faster than a person tries it."""


__all__ = [
    "Identity",
    "AccessClaims",
    "TokenPair",
    "ExternalIdentity",
    "SignInStart",
    "AuthError",
    "NotAuthenticated",
    "SignUpRefused",
    "TokenReused",
    "TooManyAttempts",
]
