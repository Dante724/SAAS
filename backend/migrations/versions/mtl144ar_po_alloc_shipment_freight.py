"""144ar — purchase_orders.alloc_basis + shipment_freight

alloc_basis: how PO charges spread across lines when capitalised into landed cost
  (value | qty | weight). shipment_freight: per-shipment freight capitalised at
  receive time, tracked apart from PO-level charge recost.

Revision ID: mtl144ar001
Revises: mtl144ap001
Create Date: 2026-09-30
"""
import sqlalchemy as sa
from alembic import op

revision = "mtl144ar001"
down_revision = "mtl144ap001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("purchase_orders", sa.Column("alloc_basis", sa.String(length=10), nullable=False, server_default="value"))
    op.add_column("purchase_orders", sa.Column("shipment_freight", sa.Float(), nullable=False, server_default="0"))


def downgrade():
    op.drop_column("purchase_orders", "shipment_freight")
    op.drop_column("purchase_orders", "alloc_basis")
