"""
The boundary between Lumen and whoever vouches for a person.

One Protocol with two steps, because sign-in is two steps: send somebody
somewhere to prove who they are, and then check what they come back with.

Everything downstream of this sees an `ExternalIdentity` and nothing else — no
provider tokens, no provider-shaped claims, no vendor SDK. That is what makes
adding a second way to sign in a new module rather than a change to how users
work, and it is the same rule every other vendor in this system lives behind.
"""

from __future__ import annotations

from typing import Protocol

from lumen.auth.contracts import ExternalIdentity, SignInStart


class IdentityProvider(Protocol):
    """
    Somewhere a person can prove who they are.

    Attributes:
        name: Which provider this is, as stored against a linked account.
    """

    name: str

    def start(self) -> SignInStart:
        """
        Where to send somebody, and what to remember while they are gone.

        The two secrets returned with the URL are held by the server, not the
        browser: one proves the person came back from the same flow they left
        on, and the other proves the code being exchanged is being exchanged
        by whoever started it.
        """
        ...

    def verify(self, code: str, verifier: str) -> ExternalIdentity:
        """
        Turn what somebody came back with into who they are.

        Raises:
            NotAuthenticated: The code is not usable, or what came back does
                not prove anything — a bad signature, the wrong audience, an
                expired assertion, or an address the provider has not
                confirmed the person controls.
        """
        ...


__all__ = ["IdentityProvider"]
