"""series of episodes: a series table and the series columns on runs

Revision ID: a3f9c2e17b54
Revises: 56833927ac0b
Create Date: 2026-10-03 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3f9c2e17b54"
down_revision: str | None = "56833927ac0b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "series",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("document_id", sa.String(length=32), nullable=False),
        sa.Column("flow_id", sa.String(length=100), nullable=False),
        sa.Column("flow_version", sa.String(length=20), nullable=False),
        sa.Column("plan_flow_id", sa.String(length=100), nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("format_spec_json", sa.JSON(), nullable=False),
        sa.Column("audience_spec_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("plan_artifact_hash", sa.String(length=64), nullable=True),
        sa.Column("checks_json", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_series_document_id"), "series", ["document_id"], unique=False)
    op.create_index(op.f("ix_series_status"), "series", ["status"], unique=False)

    # Existing runs belong to no series: every new column is nullable.
    with op.batch_alter_table("runs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("series_id", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("episode_index", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("context_hash", sa.String(length=64), nullable=True))
        batch_op.create_index(batch_op.f("ix_runs_series_id"), ["series_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_runs_series_id", "series", ["series_id"], ["id"], ondelete="CASCADE"
        )


def downgrade() -> None:
    with op.batch_alter_table("runs", schema=None) as batch_op:
        batch_op.drop_constraint("fk_runs_series_id", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_runs_series_id"))
        batch_op.drop_column("context_hash")
        batch_op.drop_column("episode_index")
        batch_op.drop_column("series_id")

    op.drop_index(op.f("ix_series_status"), table_name="series")
    op.drop_index(op.f("ix_series_document_id"), table_name="series")
    op.drop_table("series")
