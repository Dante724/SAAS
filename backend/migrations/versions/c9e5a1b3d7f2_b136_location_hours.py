"""b136 location working hours

Revision ID: c9e5a1b3d7f2
Revises: b7c4e2f1a9d3
Create Date: 2026-07-18 16:30:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'c9e5a1b3d7f2'
down_revision = 'b7c4e2f1a9d3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('locations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('working_hours', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('locations', schema=None) as batch_op:
        batch_op.drop_column('working_hours')
