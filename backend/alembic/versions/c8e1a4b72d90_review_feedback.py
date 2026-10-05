"""feedback on frozen review links

Revision ID: c8e1a4b72d90
Revises: 4d71f6829ac3
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "c8e1a4b72d90"
down_revision = "4d71f6829ac3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "review_feedback",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "review_link_id",
            sa.String(32),
            sa.ForeignKey("review_links.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("label", sa.String(40), nullable=True),
        sa.Column("stars", sa.Float(), nullable=True),
        sa.Column("worked", sa.Text(), nullable=True),
        sa.Column("did_not", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("review_link_id", "key_hash", name="uq_review_feedback_writer"),
    )
    op.create_index("ix_review_feedback_review_link_id", "review_feedback", ["review_link_id"])
    op.create_table(
        "review_feedback_marks",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "feedback_id",
            sa.String(32),
            sa.ForeignKey("review_feedback.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("episode_index", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("reaction", sa.String(16), nullable=False),
        sa.Column("slop", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "feedback_id", "episode_index", "ordinal", name="uq_review_feedback_mark"
        ),
    )
    op.create_index(
        "ix_review_feedback_marks_feedback_id", "review_feedback_marks", ["feedback_id"]
    )


def downgrade() -> None:
    op.drop_table("review_feedback_marks")
    op.drop_table("review_feedback")
