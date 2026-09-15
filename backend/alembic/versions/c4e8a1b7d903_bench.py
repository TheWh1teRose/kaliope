"""node testing bench

Revision ID: c4e8a1b7d903
Revises: 7a3d5c1e0b92
Create Date: 2026-08-16 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4e8a1b7d903"
down_revision: str | None = "7a3d5c1e0b92"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bench_runs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("document_id", sa.String(length=32), nullable=True),
        sa.Column("nodes_json", sa.JSON(), nullable=False),
        sa.Column("seeds_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_by", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("total_cost_usd", sa.Float(), nullable=False),
        sa.Column("manifest_json", sa.JSON(), nullable=True),
        sa.Column("bag_hashes_json", sa.JSON(), nullable=False),
        sa.Column("force", sa.Boolean(), nullable=False),
        sa.Column("verdict", sa.String(length=40), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_bench_runs_document_id"), "bench_runs", ["document_id"], unique=False)
    op.create_index(op.f("ix_bench_runs_status"), "bench_runs", ["status"], unique=False)

    op.create_table(
        "bench_nodes",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("bench_run_id", sa.String(length=32), nullable=False),
        sa.Column("node_name", sa.String(length=100), nullable=False),
        sa.Column("node_version", sa.String(length=20), nullable=False),
        sa.Column("cache_key", sa.String(length=64), nullable=False),
        sa.Column("cache_hit", sa.Boolean(), nullable=False),
        sa.Column("artifact_hash", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=False),
        sa.Column("tokens_out", sa.Integer(), nullable=False),
        sa.Column("model_id", sa.String(length=100), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["bench_run_id"], ["bench_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bench_run_id", "node_name", name="uq_bench_node"),
    )
    op.create_index(op.f("ix_bench_nodes_bench_run_id"), "bench_nodes", ["bench_run_id"], unique=False)
    op.create_index(op.f("ix_bench_nodes_cache_key"), "bench_nodes", ["cache_key"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_bench_nodes_cache_key"), table_name="bench_nodes")
    op.drop_index(op.f("ix_bench_nodes_bench_run_id"), table_name="bench_nodes")
    op.drop_table("bench_nodes")
    op.drop_index(op.f("ix_bench_runs_status"), table_name="bench_runs")
    op.drop_index(op.f("ix_bench_runs_document_id"), table_name="bench_runs")
    op.drop_table("bench_runs")
