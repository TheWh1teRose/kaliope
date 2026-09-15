"""editable, versioned flows and format specs

Revision ID: 7a3d5c1e0b92
Revises: 2f1a7c4b9d30
Create Date: 2026-08-14 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '7a3d5c1e0b92'
down_revision: str | None = '2f1a7c4b9d30'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('flows', schema=None) as batch_op:
        batch_op.add_column(sa.Column('name', sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column('spec_json', sa.JSON(), nullable=True))
        batch_op.add_column(
            sa.Column('revision', sa.Integer(), nullable=False, server_default='0')
        )
        batch_op.add_column(
            sa.Column('origin', sa.String(length=10), nullable=False, server_default='file')
        )
        batch_op.add_column(
            sa.Column('archived', sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('updated_by', sa.String(length=32), nullable=True))
        batch_op.create_foreign_key('fk_flows_updated_by', 'users', ['updated_by'], ['id'])

    with op.batch_alter_table('runs', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('flow_revision', sa.Integer(), nullable=False, server_default='0')
        )

    op.create_table(
        'flow_versions',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('flow_id', sa.String(length=100), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('version', sa.String(length=20), nullable=False),
        sa.Column('spec_json', sa.JSON(), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('restored_from', sa.Integer(), nullable=True),
        sa.Column('created_by', sa.String(length=32), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
        sa.ForeignKeyConstraint(['flow_id'], ['flows.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('flow_id', 'revision', name='uq_flow_revision'),
    )
    with op.batch_alter_table('flow_versions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_flow_versions_flow_id'), ['flow_id'], unique=False)

    op.create_table(
        'format_specs',
        sa.Column('id', sa.String(length=100), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('spec_json', sa.JSON(), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('origin', sa.String(length=10), nullable=False, server_default='file'),
        sa.Column('archived', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_by', sa.String(length=32), nullable=True),
        sa.ForeignKeyConstraint(['updated_by'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'format_versions',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('format_id', sa.String(length=100), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('spec_json', sa.JSON(), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('restored_from', sa.Integer(), nullable=True),
        sa.Column('created_by', sa.String(length=32), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
        sa.ForeignKeyConstraint(['format_id'], ['format_specs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('format_id', 'revision', name='uq_format_revision'),
    )
    with op.batch_alter_table('format_versions', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_format_versions_format_id'), ['format_id'], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table('format_versions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_format_versions_format_id'))
    op.drop_table('format_versions')
    op.drop_table('format_specs')

    with op.batch_alter_table('flow_versions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_flow_versions_flow_id'))
    op.drop_table('flow_versions')

    with op.batch_alter_table('runs', schema=None) as batch_op:
        batch_op.drop_column('flow_revision')

    with op.batch_alter_table('flows', schema=None) as batch_op:
        batch_op.drop_constraint('fk_flows_updated_by', type_='foreignkey')
        batch_op.drop_column('updated_by')
        batch_op.drop_column('updated_at')
        batch_op.drop_column('archived')
        batch_op.drop_column('origin')
        batch_op.drop_column('revision')
        batch_op.drop_column('spec_json')
        batch_op.drop_column('name')
