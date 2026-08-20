"""identity: a user becomes a row rather than a setting

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-20

Until now "the user" was an environment variable. Every table in this
database has carried a user_id column since it was first built, and there has
never been anything to put in it — every request was the same person because
there was no notion of a different one.

Three tables, and the split between them is the design.

A **user** is the person. Their identifier is generated here and never
derived from anything about them: it becomes a foreign key in seven existing
tables, and later a directory name and a search-index name, all of which make
it permanent. The two obvious alternatives are both mutable — people change
email addresses, and a sign-in provider's identifier is stable only for as
long as the account exists with that provider.

An **identity** is an account they sign in with. Kept separate because a
person is not their Google account: adding a second way to sign in, or
letting somebody move from a personal to a work account without losing five
years of history, is then a row rather than a migration.

A **refresh token** is a session they are holding. The token itself is never
stored — only a hash of it — so reading this table cannot produce a working
session. The column recording what a token was exchanged for is what makes
theft detectable: a token presented after it has already been exchanged is
either stolen or a race, and both are answered by ending the whole chain.

token_version on the user is how every session is ended at once. Access
tokens are deliberately not checkable against a database — that is the point
of them — so revocation is a number they carry that stops matching.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "users",
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("avatar_url", sa.String(length=1024), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="ACTIVE"),
        # Bumped to end every outstanding session at once, without a list of
        # revoked tokens for every request to consult.
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="1"),
        sa.PrimaryKeyConstraint("user_id"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    op.create_table(
        "user_identities",
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("subject", sa.String(length=256), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("email_at_link", sa.String(length=320), nullable=False, server_default=""),
        sa.Column(
            "linked_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
        # The provider's identifier for somebody is unique within that
        # provider and means nothing outside it, so the pair is the key.
        sa.PrimaryKeyConstraint("provider", "subject"),
    )
    op.create_index(
        "ix_user_identities_user_id", "user_identities", ["user_id"], unique=False
    )

    op.create_table(
        "refresh_tokens",
        sa.Column("token_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        # A hash. The token itself is never written down.
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "issued_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        # What this token was exchanged for. Set means it has been used, and
        # a used token arriving again is the signal that something is wrong.
        sa.Column("rotated_to", sa.String(length=64), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        # Hashed, so a list of somebody's sessions is possible without this
        # database holding a history of where they have been.
        sa.Column("ip_hash", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("token_id"),
    )
    op.create_index(
        "ix_refresh_tokens_hash", "refresh_tokens", ["token_hash"], unique=True
    )
    op.create_index(
        "ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_refresh_tokens_user_id", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_hash", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
    op.drop_index("ix_user_identities_user_id", table_name="user_identities")
    op.drop_table("user_identities")
    op.drop_table("users")
