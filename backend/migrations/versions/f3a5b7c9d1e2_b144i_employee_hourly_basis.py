"""b144i employee hourly basis (labour hours vs shop attendance)

Revision ID: f3a5b7c9d1e2
Revises: e2f4a6c8b1d0
Create Date: 2026-07-18 19:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = 'f3a5b7c9d1e2'
down_revision = 'e2f4a6c8b1d0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('employees', schema=None) as batch_op:
        batch_op.add_column(sa.Column('hourly_basis', sa.String(length=16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('employees', schema=None) as batch_op:
        batch_op.drop_column('hourly_basis')
