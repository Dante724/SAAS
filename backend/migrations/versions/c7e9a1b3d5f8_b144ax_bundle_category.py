"""b144ax — category_id on bundles

Revision ID: c7e9a1b3d5f8
Revises: b6d8f0a2c4e9
Create Date: 2026-08-14
"""
from alembic import op
import sqlalchemy as sa

revision = "c7e9a1b3d5f8"
down_revision = "b6d8f0a2c4e9"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("bundles") as batch_op:
        batch_op.add_column(sa.Column("category_id", sa.String(), nullable=True))


def downgrade():
    with op.batch_alter_table("bundles") as batch_op:
        batch_op.drop_column("category_id")
