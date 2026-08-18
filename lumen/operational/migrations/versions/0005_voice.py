"""voice: marking which turns were spoken rather than typed

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-18

One column, on the messages rather than on the conversation.

The extraction pipeline has always cleaned a spoken entry differently from a
typed one — hesitations, false starts, the wreckage of somebody thinking out
loud — and it has never had anything to read, because nothing could speak.
Now something can.

Per message rather than per conversation because a day where somebody typed
some turns and spoke others is the ordinary case, not the exception. Marking
the whole day as spoken would run transcript cleaning over text that was
carefully typed, which is a good way to damage the clearest writing in the
entry.

Everything already stored counts as typed, which is true: until this
migration there was no other way to say anything.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "buffer_messages",
        sa.Column(
            "modality",
            sa.String(length=16),
            nullable=False,
            server_default="TEXT",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("buffer_messages", "modality")
