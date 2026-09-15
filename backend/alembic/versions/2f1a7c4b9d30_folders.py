"""folders, gate measurements

Revision ID: 2f1a7c4b9d30
Revises: 18c88ecac754
Create Date: 2026-08-14 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '2f1a7c4b9d30'
down_revision: str | None = '18c88ecac754'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'folders',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('parent_id', sa.String(length=32), nullable=True),
        sa.Column('created_by', sa.String(length=32), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
        sa.ForeignKeyConstraint(['parent_id'], ['folders.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('folders', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_folders_parent_id'), ['parent_id'], unique=False)

    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.add_column(sa.Column('folder_id', sa.String(length=32), nullable=True))
        batch_op.create_index(batch_op.f('ix_documents_folder_id'), ['folder_id'], unique=False)
        batch_op.create_foreign_key(
            'fk_documents_folder_id', 'folders', ['folder_id'], ['id'], ondelete='SET NULL'
        )

    # Gate results kept only the verdict and the violations. A skipped gate lost
    # the reason it skipped, and a passing gate showed nothing it had measured.
    with op.batch_alter_table('gate_results', schema=None) as batch_op:
        batch_op.add_column(sa.Column('skip_reason', sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column('measurements_json', sa.JSON(), nullable=False, server_default='{}')
        )


def downgrade() -> None:
    with op.batch_alter_table('gate_results', schema=None) as batch_op:
        batch_op.drop_column('measurements_json')
        batch_op.drop_column('skip_reason')

    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.drop_constraint('fk_documents_folder_id', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_documents_folder_id'))
        batch_op.drop_column('folder_id')

    with op.batch_alter_table('folders', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_folders_parent_id'))

    op.drop_table('folders')
