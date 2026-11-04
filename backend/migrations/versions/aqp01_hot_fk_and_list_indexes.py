"""Audit 29 Oct 2026 (quality and performance) — indexes on hot foreign keys and list orderings.

- Single-column indexes on the part/order/bin foreign keys that joins and per-order lookups
  filter by (PO lines, reservations, stocktake lines, generated documents, tasks, appointments,
  chat threads, every bin_id, and the remaining document-line part_ids).
- (org_id, created_at) on postings, write_offs, client_returns and refunds: their lists read
  "this shop, newest first, limit 200".

On Postgres they are built CONCURRENTLY so writes aren't blocked during the deploy.
Index names follow the metadata naming convention (ix_<table>_<column>), matching what the
models declare, so the model<->migration drift gate stays clean.

Revision ID: aqp01
Revises: est01
"""
from alembic import op

revision = "aqp01"
down_revision = "est01"
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


def _all():
    for table, col in INDEXES:
        yield f"ix_{table}_{col}", table, f"({col})"
    for table in COMPOSITES:
        yield f"ix_{table}_org_id_created_at", table, "(org_id, created_at)"


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        # CONCURRENTLY: busy tables keep taking writes while the index builds (as ix144dh01).
        # IF NOT EXISTS makes a half-finished run safe to retry.
        with op.get_context().autocommit_block():
            for name, table, cols in _all():
                op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON {table} {cols}")
    else:
        for name, table, cols in _all():
            op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} {cols}")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            for name, _table, _cols in reversed(list(_all())):
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
    else:
        for name, _table, _cols in reversed(list(_all())):
            op.execute(f"DROP INDEX IF EXISTS {name}")
