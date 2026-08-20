"""
Storing who somebody is, and which sessions they hold.

Three tables and one deliberate asymmetry: a user and their sign-in accounts
are written plainly, and their sessions are written as hashes. Reading this
store tells you who exists; it does not let you become any of them.

Everything here is narrow on purpose. There is no general "update a user"
method, because the only things that legitimately change are the ones with
their own named operation — somebody's name after they change it with their
provider, when they were last seen, and which generation of session is
current. A general setter would eventually be pointed at `user_id`, which is
the one column nothing may ever change.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Protocol

from lumen.operational.enums import AuthProvider, UserStatus
from lumen.operational.schemas import StoredSession, UserRecord

logger = logging.getLogger(__name__)


class IdentityRepository(Protocol):
    """Where people, their sign-in accounts and their sessions are kept."""

    def find_user(self, user_id: str) -> UserRecord | None:
        """One person by identifier, or nothing."""
        ...

    def find_by_email(self, email: str) -> UserRecord | None:
        """One person by address, matched without regard to case."""
        ...

    def find_by_identity(self, provider: AuthProvider, subject: str) -> UserRecord | None:
        """
        The person who signs in with this account.

        The lookup that runs on every sign-in: the provider says who somebody
        is in its own terms, and this turns that into who they are here.
        """
        ...

    def create_user(self, *, email: str, display_name: str, avatar_url: str | None) -> UserRecord:
        """Make a person. Their identifier is generated here and never chosen."""
        ...

    def link_identity(
        self, user_id: str, *, provider: AuthProvider, subject: str, email: str
    ) -> None:
        """Record that this account belongs to this person. Safe to repeat."""
        ...

    def touch(self, user_id: str, *, at: datetime, display_name: str = "") -> None:
        """
        Note that somebody has just been seen, and take their current name.

        The name is refreshed from the provider on every sign-in because it
        is theirs to change and we are not the record of it.
        """
        ...

    def bump_token_version(self, user_id: str) -> int:
        """
        End every session this person holds, at once.

        Returns the new generation. Nothing is deleted — outstanding tokens
        simply stop matching at their next use, which is what makes this
        work without a list of revoked tokens on every request.
        """
        ...

    def set_status(self, user_id: str, status: UserStatus) -> None:
        """Whether somebody may use the system."""
        ...

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    def save_session(
        self,
        *,
        token_id: str,
        user_id: str,
        token_hash: str,
        issued_at: datetime,
        expires_at: datetime,
        user_agent: str | None,
        ip_hash: str | None,
    ) -> None:
        """Record a session. The token itself is never passed in, only its hash."""
        ...

    def find_session(self, token_hash: str) -> StoredSession | None:
        """The session this token belongs to, found by its hash."""
        ...

    def mark_rotated(self, token_id: str, *, replacement: str) -> None:
        """Record what a token was exchanged for."""
        ...

    def revoke_session(self, token_id: str, *, at: datetime) -> None:
        """End one session."""
        ...

    def revoke_chain(self, token_id: str, *, at: datetime) -> int:
        """
        End every session descended from this one, and this one.

        What happens when a token is presented twice. Following the chain
        rather than ending only the token presented is the point: by then the
        thief may already hold something newer.
        """
        ...

    def revoke_all_sessions(self, user_id: str, *, at: datetime) -> int:
        """End every session this person holds. Returns how many."""
        ...


__all__ = ["IdentityRepository"]
