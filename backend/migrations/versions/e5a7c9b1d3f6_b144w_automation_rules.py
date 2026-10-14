"""b144w workflow automation rules

Revision ID: e5a7c9b1d3f6
Revises: d4f6a8c0e2b5
Create Date: 2026-08-06 08:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = 'e5a7c9b1d3f6'
down_revision = 'd4f6a8c0e2b5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'automation_rules',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('org_id', sa.String(), nullable=False),
        sa.Column('name', sa.String(length=140), nullable=False),
        sa.Column('event', sa.String(length=32), nullable=False),
        sa.Column('trigger_status', sa.String(length=60), nullable=True),
        sa.Column('conditions', sa.Text(), nullable=True),
        sa.Column('action_type', sa.String(length=24), nullable=False),
        sa.Column('action_config', sa.Text(), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_fired_at', sa.DateTime(), nullable=True),
        sa.Column('fire_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['org_id'], ['organisations.id'], name='fk_automation_rules_org_id'),
        sa.PrimaryKeyConstraint('id', name='pk_automation_rules'),
    )
    op.create_index('ix_automation_rules_org_id', 'automation_rules', ['org_id'])
    op.create_index('ix_automation_rules_event', 'automation_rules', ['event'])


def downgrade() -> None:
    op.drop_index('ix_automation_rules_event', table_name='automation_rules')
    op.drop_index('ix_automation_rules_org_id', table_name='automation_rules')
    op.drop_table('automation_rules')
