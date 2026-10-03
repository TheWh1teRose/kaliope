"""audio takes and per-format voice casts

Revision ID: b7d2e9a4c615
Revises: f1a6c9d83e20
Create Date: 2026-10-03 16:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7d2e9a4c615"
down_revision: str | None = "f1a6c9d83e20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audio_takes",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.String(length=32), nullable=False),
        sa.Column("flow_id", sa.String(length=100), nullable=False),
        sa.Column("flow_version", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("voice_cast_json", sa.JSON(), nullable=False),
        sa.Column("script_hash", sa.String(length=64), nullable=False),
        sa.Column("manifest_json", sa.JSON(), nullable=True),
        sa.Column("total_cost_usd", sa.Float(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.String(length=32), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_audio_takes_run_id"), "audio_takes", ["run_id"], unique=False)
    op.create_index(op.f("ix_audio_takes_status"), "audio_takes", ["status"], unique=False)
    op.create_table(
        "voice_casts",
        sa.Column("format_id", sa.String(length=100), nullable=False),
        sa.Column("cast_json", sa.JSON(), nullable=False),
        sa.Column("updated_by", sa.String(length=32), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("format_id"),
    )


def downgrade() -> None:
    op.drop_table("voice_casts")
    op.drop_index(op.f("ix_audio_takes_status"), table_name="audio_takes")
    op.drop_index(op.f("ix_audio_takes_run_id"), table_name="audio_takes")
    op.drop_table("audio_takes")
