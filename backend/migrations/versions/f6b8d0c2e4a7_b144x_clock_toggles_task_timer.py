"""b144x org clock toggles + task timer

Revision ID: f6b8d0c2e4a7
Revises: e5a7c9b1d3f6
Create Date: 2026-08-06 09:30:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = 'f6b8d0c2e4a7'
down_revision = 'e5a7c9b1d3f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('organisations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('clock_wo_enabled', sa.Boolean(), nullable=False, server_default=sa.true()))
        batch_op.add_column(sa.Column('clock_attendance_enabled', sa.Boolean(), nullable=False, server_default=sa.true()))
    with op.batch_alter_table('time_entries', schema=None) as batch_op:
        batch_op.add_column(sa.Column('task_id', sa.String(), nullable=True))
        batch_op.create_index('ix_time_entries_task_id', ['task_id'])


def downgrade() -> None:
    with op.batch_alter_table('time_entries', schema=None) as batch_op:
        batch_op.drop_index('ix_time_entries_task_id')
        batch_op.drop_column('task_id')
    with op.batch_alter_table('organisations', schema=None) as batch_op:
        batch_op.drop_column('clock_attendance_enabled')
        batch_op.drop_column('clock_wo_enabled')
