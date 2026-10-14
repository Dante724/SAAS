"""b144du — multi-order invoices (an invoice can bill several work orders)

- invoice_orders link table (invoice_id, order_id, sort_order) with a unique
  (invoice_id, order_id) constraint.

Revision ID: u1w3y5a7c9e1
Revises: t0v2x4z6b8d0
Create Date: 2026-09-02
"""
from alembic import op
import sqlalchemy as sa

revision = "u1w3y5a7c9e1"
down_revision = "t0v2x4z6b8d0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "invoice_orders",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("invoice_id", sa.String(), sa.ForeignKey("invoices.id"), nullable=False),
        sa.Column("order_id", sa.String(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("invoice_id", "order_id", name="uq_invoice_order"),
    )
    op.create_index("ix_invoice_orders_invoice_id", "invoice_orders", ["invoice_id"])
    op.create_index("ix_invoice_orders_order_id", "invoice_orders", ["order_id"])
    # backfill: every invoice that already points at an order gets a link row
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, order_id FROM invoices WHERE order_id IS NOT NULL")).fetchall()
    for inv_id, order_id in rows:
        conn.execute(sa.text(
            "INSERT INTO invoice_orders (id, invoice_id, order_id, sort_order) "
            "VALUES (:i, :inv, :o, 0)"),
            {"i": inv_id + "-lnk", "inv": inv_id, "o": order_id})


def downgrade():
    op.drop_index("ix_invoice_orders_order_id", table_name="invoice_orders")
    op.drop_index("ix_invoice_orders_invoice_id", table_name="invoice_orders")
    op.drop_table("invoice_orders")
