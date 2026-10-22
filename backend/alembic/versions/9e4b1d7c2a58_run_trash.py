"""trash marker on runs

Revision ID: 9e4b1d7c2a58
Revises: c8e1a4b72d90
Create Date: 2026-10-22 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9e4b1d7c2a58"
down_revision: str | None = "c8e1a4b72d90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing runs stay out of the trash: the column stays null.
    with op.batch_alter_table("runs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("trashed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("runs", schema=None) as batch_op:
        batch_op.drop_column("trashed_at")
