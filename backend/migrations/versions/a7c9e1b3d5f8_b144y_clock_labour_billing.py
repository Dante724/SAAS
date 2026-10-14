"""b144y optional time-based labour billing toggle

Revision ID: a7c9e1b3d5f8
Revises: f6b8d0c2e4a7
Create Date: 2026-08-06 10:15:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = 'a7c9e1b3d5f8'
down_revision = 'f6b8d0c2e4a7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('organisations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('clock_labour_billing', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table('organisations', schema=None) as batch_op:
        batch_op.drop_column('clock_labour_billing')
