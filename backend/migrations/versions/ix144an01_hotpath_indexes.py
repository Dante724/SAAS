"""Build 144an — hot-path FK / tenant-key indexes.

Postgres does not auto-index foreign keys, so the hottest tenant-key and FK columns
(order/invoice/payment/transaction lookups and joins) full-scan at scale. This migration
adds single-column indexes to those columns. The models carry matching ``index=True`` so
dev (SQLite) builds them via create_all; this migration owns them for production.

Each create/drop checks the live schema first, so a pre-existing index name (e.g. already built from the model
on a freshly created DB) doesn't abort the migration.

Revision ID: ix144an01
Revises: ip144ak01
"""
from alembic import op

revision = "ix144an01"
down_revision = "ip144ak01"
branch_labels = None
depends_on = None


# (table, column) pairs — index name is always ix_<table>_<column>.
_INDEXES = [
    ("orders", "org_id"),
    ("orders", "client_id"),
    ("orders", "device_id"),
    ("orders", "location_id"),
    ("orders", "assigned_to_id"),
    ("orders", "manager_id"),
    ("orders", "type_id"),
    ("order_lines", "order_id"),
    ("order_lines", "technician_id"),
    ("payments", "invoice_id"),
    ("payments", "client_id"),
    ("payments", "order_id"),
    ("payments", "account_id"),
    ("invoices", "org_id"),
    ("invoices", "order_id"),
    ("invoices", "client_id"),
    ("invoice_lines", "invoice_id"),
    ("transactions", "org_id"),
    ("transactions", "account_id"),
    ("transactions", "employee_id"),
    ("order_status_logs", "order_id"),
    ("order_comments", "order_id"),
    ("expenses", "org_id"),
    ("sales", "org_id"),
]



def _indexes(table: str) -> set:
    """Index names that exist right now. Checking first (instead of try/except around DDL) matters
    on Postgres: a failed statement aborts the whole migration transaction, so the except can't
    recover and the upgrade fails later with a confusing error."""
    import sqlalchemy as _sa
    insp = _sa.inspect(op.get_bind())
    if not insp.has_table(table):
        return set()
    return {i["name"] for i in insp.get_indexes(table)}

def upgrade() -> None:
    for table, col in _INDEXES:
        if f"ix_{table}_{col}" not in _indexes(table):
            op.create_index(op.f(f"ix_{table}_{col}"), table, [col])


def downgrade() -> None:
    for table, col in _INDEXES:
        if f"ix_{table}_{col}" in _indexes(table):
            op.drop_index(op.f(f"ix_{table}_{col}"), table_name=table)
