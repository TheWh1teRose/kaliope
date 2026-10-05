"""revocable snapshot review links

Revision ID: 4d71f6829ac3
Revises: b7d2e9a4c615
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "4d71f6829ac3"
down_revision = "b7d2e9a4c615"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "review_links",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("target_kind", sa.String(10), nullable=False),
        sa.Column("target_id", sa.String(32), nullable=False),
        sa.Column("created_by", sa.String(32), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("token_digest", sa.String(64), unique=True, nullable=False),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
    )
    op.create_index(
        "uq_review_link_active_target",
        "review_links",
        ["target_kind", "target_id"],
        unique=True,
        sqlite_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("review_links")
