"""b144n document template page size + font

Revision ID: c3e5a7b9d1f4
Revises: b2d4f6a8c0e1
Create Date: 2026-07-18 22:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = 'c3e5a7b9d1f4'
down_revision = 'b2d4f6a8c0e1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('document_templates', schema=None) as batch_op:
        batch_op.add_column(sa.Column('page_size', sa.String(length=12), nullable=False,
                                      server_default='a4'))
        batch_op.add_column(sa.Column('font_family', sa.String(length=40), nullable=False,
                                      server_default='Sans-Serif'))


def downgrade() -> None:
    with op.batch_alter_table('document_templates', schema=None) as batch_op:
        batch_op.drop_column('font_family')
        batch_op.drop_column('page_size')
