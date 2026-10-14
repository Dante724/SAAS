"""b144k richer employee profile (position, tax id, public name, note)

Revision ID: b2d4f6a8c0e1
Revises: a1c3e5f7b9d2
Create Date: 2026-07-18 21:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = 'b2d4f6a8c0e1'
down_revision = 'a1c3e5f7b9d2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('employees', schema=None) as batch_op:
        batch_op.add_column(sa.Column('position', sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column('tax_id', sa.String(length=60), nullable=True))
        batch_op.add_column(sa.Column('public_name', sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column('note', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('employees', schema=None) as batch_op:
        batch_op.drop_column('note')
        batch_op.drop_column('public_name')
        batch_op.drop_column('tax_id')
        batch_op.drop_column('position')
