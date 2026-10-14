"""b144j employee clock visibility (job hours / shop attendance)

Revision ID: a1c3e5f7b9d2
Revises: f3a5b7c9d1e2
Create Date: 2026-07-18 20:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = 'a1c3e5f7b9d2'
down_revision = 'f3a5b7c9d1e2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('employees', schema=None) as batch_op:
        batch_op.add_column(sa.Column('track_job_hours', sa.Boolean(), nullable=False,
                                      server_default=sa.true()))
        batch_op.add_column(sa.Column('track_attendance', sa.Boolean(), nullable=False,
                                      server_default=sa.true()))


def downgrade() -> None:
    with op.batch_alter_table('employees', schema=None) as batch_op:
        batch_op.drop_column('track_attendance')
        batch_op.drop_column('track_job_hours')
