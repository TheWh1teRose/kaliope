"""experiments: runs and collected outputs

Revision ID: e5a1c7d93b40
Revises: d9b2f4a60e17
Create Date: 2026-10-01 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5a1c7d93b40"
down_revision: str | None = "d9b2f4a60e17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "experiment_runs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("experiment_key", sa.String(length=100), nullable=False),
        sa.Column("experiment_version", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("setup_json", sa.JSON(), nullable=False),
        sa.Column("source_json", sa.JSON(), nullable=True),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column("warnings_json", sa.JSON(), nullable=False),
        sa.Column("manifest_json", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("total_cost_usd", sa.Float(), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=False),
        sa.Column("tokens_out", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_experiment_runs_key_created",
        "experiment_runs",
        ["experiment_key", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_experiment_runs_status"), "experiment_runs", ["status"], unique=False
    )

    op.create_table(
        "experiment_outputs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("experiment_key", sa.String(length=100), nullable=False),
        sa.Column("run_id", sa.String(length=32), nullable=True),
        sa.Column("item", sa.String(length=100), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=True),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("meta_json", sa.JSON(), nullable=False),
        sa.Column("setup_json", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["experiment_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "item", name="uq_experiment_output_item"),
    )
    op.create_index(
        "ix_experiment_outputs_key_created",
        "experiment_outputs",
        ["experiment_key", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_experiment_outputs_run_id"), "experiment_outputs", ["run_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_experiment_outputs_run_id"), table_name="experiment_outputs")
    op.drop_index("ix_experiment_outputs_key_created", table_name="experiment_outputs")
    op.drop_table("experiment_outputs")
    op.drop_index(op.f("ix_experiment_runs_status"), table_name="experiment_runs")
    op.drop_index("ix_experiment_runs_key_created", table_name="experiment_runs")
    op.drop_table("experiment_runs")
