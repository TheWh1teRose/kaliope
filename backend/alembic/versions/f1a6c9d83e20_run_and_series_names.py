"""optional display name on runs and series

Revision ID: f1a6c9d83e20
Revises: a3f9c2e17b54
Create Date: 2026-10-03 11:50:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1a6c9d83e20"
down_revision: str | None = "a3f9c2e17b54"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing rows keep their current title: the column stays null.
    with op.batch_alter_table("runs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("name", sa.String(length=200), nullable=True))
    with op.batch_alter_table("series", schema=None) as batch_op:
        batch_op.add_column(sa.Column("name", sa.String(length=200), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("series", schema=None) as batch_op:
        batch_op.drop_column("name")
    with op.batch_alter_table("runs", schema=None) as batch_op:
        batch_op.drop_column("name")
