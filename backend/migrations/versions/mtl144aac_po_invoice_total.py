"""144aac — purchase_orders.supplier_invoice_total (three-way match)

Revision ID: mtl144aac001
Revises: mtl144v0001
Create Date: 2026-09-28
"""
import sqlalchemy as sa
from alembic import op

revision = "mtl144aac001"
down_revision = "mtl144v0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("purchase_orders", sa.Column("supplier_invoice_total", sa.Float(), nullable=True))


def downgrade():
    op.drop_column("purchase_orders", "supplier_invoice_total")
