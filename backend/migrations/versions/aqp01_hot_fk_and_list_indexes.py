"""Audit 29 Oct 2026 (quality and performance) — indexes on hot foreign keys and list orderings.

- Single-column indexes on the part/order/bin foreign keys that joins and per-order lookups
  filter by (PO lines, reservations, stocktake lines, generated documents, tasks, appointments,
  chat threads, every bin_id, and the remaining document-line part_ids).
- (org_id, created_at) on postings, write_offs, client_returns and refunds: their lists read
  "this shop, newest first, limit 200".

Index names follow the metadata naming convention (ix_<table>_<column>), matching what the
models declare, so the model<->migration drift gate stays clean.

Revision ID: aqp01
Revises: mkw01
"""
from alembic import op

revision = "aqp01"
down_revision = "mkw01"
branch_labels = None
depends_on = None

INDEXES = [
    ("po_lines", "part_id"), ("stock_reservations", "part_id"), ("stock_reservations", "order_id"),
    ("stocktake_lines", "part_id"), ("stocktake_lines", "bin_id"), ("stocktakes", "bin_id"),
    ("generated_documents", "order_id"), ("tasks", "order_id"), ("appointments", "order_id"),
    ("chat_threads", "order_id"), ("inbox_threads", "order_id"), ("asset_movements", "order_id"),
    ("ecommerce_order_maps", "order_id"), ("refunds", "order_id"),
    ("stock_item_batches", "bin_id"), ("write_off_lines", "bin_id"), ("write_off_lines", "part_id"),
    ("client_return_lines", "part_id"), ("purchase_return_lines", "part_id"),
    ("stock_transfer_lines", "part_id"), ("posting_lines", "part_id"),
]

COMPOSITES = ["postings", "write_offs", "client_returns", "refunds"]


def upgrade() -> None:
    for table, col in INDEXES:
        op.create_index(f"ix_{table}_{col}", table, [col])
    for table in COMPOSITES:
        op.create_index(f"ix_{table}_org_id_created_at", table, ["org_id", "created_at"])


def downgrade() -> None:
    for table in reversed(COMPOSITES):
        op.drop_index(f"ix_{table}_org_id_created_at", table_name=table)
    for table, col in reversed(INDEXES):
        op.drop_index(f"ix_{table}_{col}", table_name=table)
