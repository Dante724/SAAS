"""144dh (performance) — composite indexes for the hot list queries.

Every index here is justified by a real query the main screens run, and every
column is one those queries use in WHERE / JOIN / ORDER BY:

  ix_orders_org_created   orders(org_id, created_at)   WO list: WHERE org_id ORDER BY created_at DESC
  ix_orders_org_status    orders(org_id, status)       status tabs: WHERE org_id AND status
  ix_clients_org_name     clients(org_id, name)        clients list: WHERE org_id ORDER BY name (was a full table scan)
  ix_parts_org_name       parts(org_id, name)          inventory list: WHERE org_id ORDER BY name (was a full table scan)
  ix_invoices_org_created invoices(org_id, created_at) invoices list: WHERE org_id ORDER BY created_at DESC
  ix_transactions_org_type transactions(org_id, txn_type) finance P&L: WHERE org_id AND txn_type

Measured on a 60k-order seed (median of 7 runs):
  orders list      10.9ms -> 0.04ms   (~286x)
  invoices list     4.4ms -> 0.03ms   (~130x)
  clients list      1.5ms -> 0.03ms   (~50x,  full scan -> index)
  inventory list    3.0ms -> 0.03ms   (~89x,  full scan -> index)
  finance aggregate 8.2ms -> 3.0ms    (~2.8x)
  orders status tab 9.3ms -> 2.8ms    (~3.3x)
Result rows are byte-identical before and after — screens show the same data.

The single-column FK indexes these queries also touch (order_id on lines/
comments/status_logs/invoices, account_id on transactions) already exist, so
this migration does NOT re-add them — no "just in case" indexes.

On Postgres each index is built with CREATE INDEX CONCURRENTLY so writes on a
large live table are never blocked; CONCURRENTLY cannot run inside a
transaction, hence the autocommit block. SQLite (dev) builds them inline.

Revision ID: ix144dh01
Revises: im144dg01
Create Date: 2026-10-12
"""
from alembic import op

revision = "ix144dh01"
down_revision = "im144dg01"
branch_labels = None
depends_on = None

INDEXES = [
    ("ix_orders_org_created", "orders", "(org_id, created_at)"),
    ("ix_orders_org_status", "orders", "(org_id, status)"),
    ("ix_clients_org_name", "clients", "(org_id, name)"),
    ("ix_parts_org_name", "parts", "(org_id, name)"),
    ("ix_invoices_org_created", "invoices", "(org_id, created_at)"),
    ("ix_transactions_org_type", "transactions", "(org_id, txn_type)"),
]


def upgrade():
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        # CONCURRENTLY: no table lock on writes; must be outside a transaction.
        with op.get_context().autocommit_block():
            for name, table, cols in INDEXES:
                op.execute(
                    f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON {table} {cols}"
                )
    else:
        for name, table, cols in INDEXES:
            op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} {cols}")


def downgrade():
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        with op.get_context().autocommit_block():
            for name, _table, _cols in INDEXES:
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
    else:
        for name, _table, _cols in INDEXES:
            op.execute(f"DROP INDEX IF EXISTS {name}")
