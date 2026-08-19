"""review queue: deferring an item, and keeping what was going to be written

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-19

Three changes, all in service of making the review queue answerable.

The first is a date an item stays hidden until. Deferring something has so
far meant counting the deferral and showing it again immediately, which is
not deferring it.

The second records what was actually done about an item, so the queue can be
read back without asking the graph about every row.

The third is the important one. Until now, when the system gave up on a
decision it kept a note of what it was leaning towards and threw away the
change it had worked out — the new wording for a belief that had moved on,
the shape of a record that did not exist yet. That is enough to describe a
question and not enough to answer one, so answering meant working it all out
a second time and hoping it matched what the person had been shown. Now the
whole thing is kept, and answering replays it.

Stored as one text column rather than a table of columns because nothing
queries inside it: it is read whole, by the one decision it belongs to, or
not at all. The version number beside it is what lets the shape change later
without guessing at what an old row meant.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "hitl_queue",
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "hitl_queue",
        sa.Column("resolved_action", sa.String(length=32), nullable=True),
    )
    op.create_table(
        "hitl_proposals",
        sa.Column("audit_node_id", sa.String(length=256), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column(
            "schema_version", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["audit_node_id"],
            ["hitl_queue.audit_node_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("audit_node_id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("hitl_proposals")
    op.drop_column("hitl_queue", "resolved_action")
    op.drop_column("hitl_queue", "snoozed_until")
