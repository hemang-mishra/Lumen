"""imports: a record of every uploaded conversation

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-16

One table, added because conversations can now arrive from a file rather
than only from a live session.

A session buffer on its own cannot answer three questions that matter the
moment uploading is possible: which file this came from, whether this exact
conversation has been imported before, and which run processed it. The first
is history, the second is what stops a second upload of the same export from
running somebody's history through the pipeline twice, and the third is the
only path from an upload to the trace that explains what it did.

The uniqueness rule on (user_id, source_conversation_id) is the load-bearing
part. It is scoped to the user rather than global because two people
importing from the same application can legitimately hold the same
conversation identifier.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "imports",
        sa.Column("import_id", sa.String(length=128), nullable=False),
        sa.Column("batch_id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("source_conversation_id", sa.String(length=256), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("filename", sa.String(length=256), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("message_count", sa.Integer(), nullable=False),
        # Nullable: a duplicate never gets a buffer, and a run that fails
        # before it starts never gets a job.
        sa.Column("session_id", sa.String(length=128), nullable=True),
        sa.Column("job_id", sa.String(length=128), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("import_id"),
        sa.UniqueConstraint(
            "user_id", "source_conversation_id", name="uq_import_user_conversation"
        ),
    )
    with op.batch_alter_table("imports", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_imports_batch_id"), ["batch_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_imports_user_id"), ["user_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_imports_trace_id"), ["trace_id"], unique=False)
        # The history view is always "this user's imports, newest first".
        batch_op.create_index(
            "ix_import_user_created", ["user_id", "created_at"], unique=False
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("imports", schema=None) as batch_op:
        batch_op.drop_index("ix_import_user_created")
        batch_op.drop_index(batch_op.f("ix_imports_trace_id"))
        batch_op.drop_index(batch_op.f("ix_imports_user_id"))
        batch_op.drop_index(batch_op.f("ix_imports_batch_id"))
    op.drop_table("imports")
