"""
Who is asking.

Lumen was single-user in exactly one sense: there was no such thing as a
person. `AppConfig.user_id` was an environment variable, every route read it,
and every request was therefore the same somebody. Everything else was
further along — the operational database has been keyed by user since it was
built. What was missing was anybody to put in it.

This package is that. A person is a row; they prove who they are with an
account somewhere else, once; and everything after that is Lumen's own
short-lived token and a renewable session that is exchanged every time it is
used, so a stolen one is something that gets noticed.

What it deliberately does not do is separate anybody's data. Until per-user
stores land, every signed-in person shares one graph — which is correct for
the single-user deployment this is, and is the reason a second person must
not be invited before then.
"""

from lumen.auth.contracts import (
    AuthError,
    Identity,
    NotAuthenticated,
    SignUpRefused,
    TokenPair,
    TokenReused,
    TooManyAttempts,
)
from lumen.auth.service import AuthService

__all__ = [
    "AuthService",
    "AuthError",
    "Identity",
    "NotAuthenticated",
    "SignUpRefused",
    "TokenPair",
    "TokenReused",
    "TooManyAttempts",
]
