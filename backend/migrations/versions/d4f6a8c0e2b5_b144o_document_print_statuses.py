"""b144o document template per-status print permissions

Revision ID: d4f6a8c0e2b5
Revises: c3e5a7b9d1f4
Create Date: 2026-07-18 23:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = 'd4f6a8c0e2b5'
down_revision = 'c3e5a7b9d1f4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('document_templates', schema=None) as batch_op:
        batch_op.add_column(sa.Column('print_statuses', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('document_templates', schema=None) as batch_op:
        batch_op.drop_column('print_statuses')
