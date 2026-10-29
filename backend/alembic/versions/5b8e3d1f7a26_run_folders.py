"""folders for runs and series

Revision ID: 5b8e3d1f7a26
Revises: 3c7f2a9e6d14
Create Date: 2026-10-29 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "5b8e3d1f7a26"
down_revision: str | None = "3c7f2a9e6d14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_folders",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("parent_id", sa.String(length=32), nullable=True),
        sa.Column("created_by", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["parent_id"], ["run_folders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_run_folders_parent_id"), "run_folders", ["parent_id"], unique=False)

    # Existing runs and series stay "Ohne Ordner": the columns stay null.
    for table in ("runs", "series"):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column("folder_id", sa.String(length=32), nullable=True))
            batch_op.create_index(batch_op.f(f"ix_{table}_folder_id"), ["folder_id"], unique=False)
            batch_op.create_foreign_key(
                f"fk_{table}_folder_id", "run_folders", ["folder_id"], ["id"], ondelete="SET NULL"
            )


def downgrade() -> None:
    for table in ("series", "runs"):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_constraint(f"fk_{table}_folder_id", type_="foreignkey")
            batch_op.drop_index(batch_op.f(f"ix_{table}_folder_id"))
            batch_op.drop_column("folder_id")
    op.drop_index(op.f("ix_run_folders_parent_id"), table_name="run_folders")
    op.drop_table("run_folders")
