"""b144 employee weekly hours

Revision ID: e2f4a6c8b1d0
Revises: d1a2b3c4e5f6
Create Date: 2026-07-18 18:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = 'e2f4a6c8b1d0'
down_revision = 'd1a2b3c4e5f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('employees', schema=None) as batch_op:
        batch_op.add_column(sa.Column('weekly_hours', sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('employees', schema=None) as batch_op:
        batch_op.drop_column('weekly_hours')
