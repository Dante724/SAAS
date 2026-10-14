"""144an — purchase_orders manual workflow stage (configurable pipeline, Orderry parity)

Adds PurchaseOrder.workflow_status: a human-driven stage referencing an OrderStatus key in the
new 'purchase_order' pipeline. Independent of the receipt state (`status`), which stays the only
thing receiving touches. Backfills existing rows from their receipt state.

Revision ID: mtl144an001
Revises: mtl144aj001
Create Date: 2026-09-30
"""
import sqlalchemy as sa
from alembic import op

revision = "mtl144an001"
down_revision = "mtl144aj001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("purchase_orders", sa.Column("workflow_status", sa.String(length=50), nullable=True))
    op.execute(
        "UPDATE purchase_orders SET workflow_status = CASE "
        "WHEN status='received' THEN 'po_received' "
        "WHEN status IN ('cancelled','canceled') THEN 'po_cancelled' "
        "WHEN status='grouped' THEN 'po_closed' "
        "WHEN status='draft' THEN 'po_draft' "
        "ELSE 'po_ordered' END "
        "WHERE workflow_status IS NULL")


def downgrade():
    op.drop_column("purchase_orders", "workflow_status")
