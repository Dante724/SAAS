"""b144bi — coupon issued to a specific client (returning-customer loop)

Revision ID: c3e5b7d9f1a3
Revises: b2d4f6a8c0e2
Create Date: 2026-08-16
"""
from alembic import op
import sqlalchemy as sa

revision = "c3e5b7d9f1a3"
down_revision = "b2d4f6a8c0e2"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("coupons") as batch_op:
        batch_op.add_column(sa.Column("client_id", sa.String(), nullable=True))


def downgrade():
    with op.batch_alter_table("coupons") as batch_op:
        batch_op.drop_column("client_id")
