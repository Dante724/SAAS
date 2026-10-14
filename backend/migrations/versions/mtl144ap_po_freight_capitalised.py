"""144ap — purchase_orders.freight_capitalised (landed-cost recost tracking)

Tracks how much freight/charges has already been baked into received units' cost, so a
late-arriving freight bill only applies the delta when recosting on-hand stock.

Revision ID: mtl144ap001
Revises: mtl144an001
Create Date: 2026-09-30
"""
import sqlalchemy as sa
from alembic import op

revision = "mtl144ap001"
down_revision = "mtl144an001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("purchase_orders", sa.Column("freight_capitalised", sa.Float(), nullable=False, server_default="0"))


def downgrade():
    op.drop_column("purchase_orders", "freight_capitalised")
