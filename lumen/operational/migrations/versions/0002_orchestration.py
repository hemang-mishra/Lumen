"""orchestration: coreference maps, per-episode stage runs and writes

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-15

Two changes, both needed once one entry can produce several episodes that
are processed independently.

The stage-run and write-log tables gain the episode they belong to. Without
it, four episodes each running the same stage either collide on the
uniqueness rule or read as one stage retried three times, and there is no
way to ask what a single episode put into the graph.

The coreference map finally gets somewhere to live. Every episode in the
graph carries the id of one, and until now nothing stored them.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "coreference_maps",
        sa.Column("id", sa.String(length=256), nullable=False),
        sa.Column("job_id", sa.String(length=128), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("session_id", sa.String(length=128), nullable=False),
        sa.Column("entry_id", sa.String(length=128), nullable=False),
        sa.Column("resolved_entities", sa.JSON(), nullable=False),
        sa.Column("ambiguous_refs", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["pipeline_jobs.job_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("coreference_maps", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_coreference_maps_job_id"), ["job_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_coreference_maps_trace_id"), ["trace_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_coreference_maps_session_id"), ["session_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_coreference_maps_entry_id"), ["entry_id"], unique=False
        )

    # Existing rows predate episode tracking, so they are filled with the
    # empty marker that means "not tied to one episode" rather than left
    # null — the uniqueness rule below would not cover nulls.
    with op.batch_alter_table("pipeline_stage_runs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "episode_id",
                sa.String(length=256),
                nullable=False,
                server_default="",
            )
        )
        batch_op.create_index(
            batch_op.f("ix_pipeline_stage_runs_episode_id"),
            ["episode_id"],
            unique=False,
        )
        batch_op.drop_constraint("uq_stage_run_job_stage_attempt", type_="unique")
        batch_op.create_unique_constraint(
            "uq_stage_run_job_stage_episode_attempt",
            ["job_id", "stage", "episode_id", "attempt"],
        )

    with op.batch_alter_table("pipeline_write_log", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "episode_id",
                sa.String(length=256),
                nullable=False,
                server_default="",
            )
        )
        batch_op.create_index(
            batch_op.f("ix_pipeline_write_log_episode_id"),
            ["episode_id"],
            unique=False,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("pipeline_write_log", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_pipeline_write_log_episode_id"))
        batch_op.drop_column("episode_id")

    with op.batch_alter_table("pipeline_stage_runs", schema=None) as batch_op:
        batch_op.drop_constraint(
            "uq_stage_run_job_stage_episode_attempt", type_="unique"
        )
        batch_op.create_unique_constraint(
            "uq_stage_run_job_stage_attempt", ["job_id", "stage", "attempt"]
        )
        batch_op.drop_index(batch_op.f("ix_pipeline_stage_runs_episode_id"))
        batch_op.drop_column("episode_id")

    with op.batch_alter_table("coreference_maps", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_coreference_maps_entry_id"))
        batch_op.drop_index(batch_op.f("ix_coreference_maps_session_id"))
        batch_op.drop_index(batch_op.f("ix_coreference_maps_trace_id"))
        batch_op.drop_index(batch_op.f("ix_coreference_maps_job_id"))
    op.drop_table("coreference_maps")
