"""Build 144q — deposit_amount on orders (estimate deposits / pay-link).

Adds a nullable-with-default float column so the client can be asked for a
partial deposit up front; the amount is paid through the existing pay-link
(POST /api/payments/checkout).

Revision ID: dep144q0001
Revises: g2b4d6f8h0j2
"""
import sqlalchemy as sa
from alembic import op

revision = "dep144q0001"
down_revision = "g2b4d6f8h0j2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("orders") as b:
        b.add_column(sa.Column("deposit_amount", sa.Float(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("orders") as b:
        b.drop_column("deposit_amount")
