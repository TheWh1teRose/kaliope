"""trash marker on series

Revision ID: 3c7f2a9e6d14
Revises: 9e4b1d7c2a58
Create Date: 2026-10-26 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "3c7f2a9e6d14"
down_revision: str | None = "9e4b1d7c2a58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing series stay out of the trash: the column stays null.
    with op.batch_alter_table("series", schema=None) as batch_op:
        batch_op.add_column(sa.Column("trashed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("series", schema=None) as batch_op:
        batch_op.drop_column("trashed_at")
