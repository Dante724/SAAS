"""b143 message templates

Revision ID: d1a2b3c4e5f6
Revises: c9e5a1b3d7f2
Create Date: 2026-07-18 17:40:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'd1a2b3c4e5f6'
down_revision = 'c9e5a1b3d7f2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'message_templates',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('org_id', sa.String(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('channel', sa.String(length=16), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['organisations.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_message_templates_org_id'), 'message_templates', ['org_id'])


def downgrade() -> None:
    op.drop_index(op.f('ix_message_templates_org_id'), table_name='message_templates')
    op.drop_table('message_templates')
