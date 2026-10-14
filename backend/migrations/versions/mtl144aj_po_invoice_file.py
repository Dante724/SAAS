"""144aj — purchase_orders supplier invoice file (blob pointer)

Revision ID: mtl144aj001
Revises: mtl144ah001
Create Date: 2026-09-28
"""
import sqlalchemy as sa
from alembic import op

revision = "mtl144aj001"
down_revision = "mtl144ah001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("purchase_orders", sa.Column("supplier_invoice_blob_id", sa.String(), nullable=True))
    op.add_column("purchase_orders", sa.Column("supplier_invoice_name", sa.String(length=200), nullable=True))


def downgrade():
    op.drop_column("purchase_orders", "supplier_invoice_name")
    op.drop_column("purchase_orders", "supplier_invoice_blob_id")
