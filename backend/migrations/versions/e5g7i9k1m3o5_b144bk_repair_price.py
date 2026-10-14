"""b144bk — store price vs repair price on products

Revision ID: e5b7d9f1a3c5
Revises: d4f6a8c0e2b4
Create Date: 2026-08-17
"""
from alembic import op
import sqlalchemy as sa

revision = "e5b7d9f1a3c5"
down_revision = "d4f6a8c0e2b4"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("parts") as batch_op:
        batch_op.add_column(sa.Column("repair_price", sa.Float(), nullable=False, server_default="0"))


def downgrade():
    with op.batch_alter_table("parts") as batch_op:
        batch_op.drop_column("repair_price")
