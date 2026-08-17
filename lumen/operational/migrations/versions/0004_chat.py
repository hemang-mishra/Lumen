"""chat: conversations that branch, and remember themselves

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-17

Five columns, added because a conversation can now be held here rather than
only collected here.

Two of them make a conversation a tree instead of a list. Editing a message
has to be possible without destroying what was there — the same instinct as
the graph's append-only rule — so an edit is written as a sibling of the
message it replaces, and the buffer names which end of which branch the
person is actually in. Everything else stays exactly where it was and stays
readable.

The other two are memory. A long conversation cannot be re-read from the
beginning on every turn, so what it has been about is written down every so
often and kept. Storing it rather than holding it in memory is what makes a
chat survive a restart, and what stops the same summary being paid for twice.

Nothing here is required of an imported conversation. Those arrive linear
and stay linear: no parent links, no active pointer, and reading them is
unchanged.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # No foreign key: this points inside its own table, and a self-reference
    # would make a whole conversation impossible to remove in one statement.
    op.add_column(
        "buffer_messages",
        sa.Column("parent_message_id", sa.String(length=128), nullable=True),
    )
    op.create_index(
        "ix_buffer_messages_parent_message_id",
        "buffer_messages",
        ["parent_message_id"],
    )

    op.add_column(
        "session_buffers",
        sa.Column("active_message_id", sa.String(length=128), nullable=True),
    )
    op.add_column("session_buffers", sa.Column("rolling_summary", sa.Text(), nullable=True))
    # Rows written before this migration have no summary, and zero is the
    # honest description of how far one has been written: not at all.
    op.add_column(
        "session_buffers",
        sa.Column(
            "summary_through_seq", sa.Integer(), nullable=False, server_default="0"
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("session_buffers", "summary_through_seq")
    op.drop_column("session_buffers", "rolling_summary")
    op.drop_column("session_buffers", "active_message_id")
    op.drop_index("ix_buffer_messages_parent_message_id", table_name="buffer_messages")
    op.drop_column("buffer_messages", "parent_message_id")
