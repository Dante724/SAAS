"""b144aa product minimum order quantity

Revision ID: b8d0f2a4c6e9
Revises: a7c9e1b3d5f8
Create Date: 2026-08-06 11:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = 'b8d0f2a4c6e9'
down_revision = 'a7c9e1b3d5f8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('parts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('min_order_qty', sa.Float(), nullable=False, server_default='0'))


def downgrade() -> None:
    with op.batch_alter_table('parts', schema=None) as batch_op:
        batch_op.drop_column('min_order_qty')
