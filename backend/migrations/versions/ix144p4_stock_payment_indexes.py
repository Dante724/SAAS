"""Security/perf phase 4 — indexes for the hottest unindexed lookups.

- stock_levels had no part_id index at all: every sale, posting and stock check scanned the table.
- stock_movements had no part_id index: product history and FIFO/last-movement lookups scanned.
- payments.paid_at, audit_log(org_id, created_at), notification_logs(org_id, created_at): the
  dashboard "today" totals, audit viewer and message log filter on these.

On Postgres they're built CONCURRENTLY (outside the migration transaction) so a large live
table isn't write-locked while the index builds. Each create/drop checks the live schema first.

Revision ID: ix144p4
Revises: mf144p2
"""
import sqlalchemy as sa
from alembic import op

revision = "ix144p4"
down_revision = "mf144p2"
branch_labels = None
depends_on = None

_INDEXES = [
    ("stock_levels", "ix_stock_levels_part_location", ["part_id", "location_id"]),
    ("stock_movements", "ix_stock_movements_part_created", ["part_id", "created_at"]),
    ("payments", "ix_payments_paid_at", ["paid_at"]),
    ("audit_log", "ix_audit_log_org_created", ["org_id", "created_at"]),
    ("notification_logs", "ix_notification_logs_org_created", ["org_id", "created_at"]),
]


def _indexes(table: str) -> set:
    insp = sa.inspect(op.get_bind())
    if not insp.has_table(table):
        return set()
    return {i["name"] for i in insp.get_indexes(table)}


def upgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"
    todo = [(t, n, c) for t, n, c in _INDEXES if n not in _indexes(t) and sa.inspect(op.get_bind()).has_table(t)]
    if pg:
        with op.get_context().autocommit_block():
            for table, name, cols in todo:
                op.create_index(name, table, cols, postgresql_concurrently=True, if_not_exists=True)
    else:
        for table, name, cols in todo:
            op.create_index(name, table, cols)


def downgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"
    todo = [(t, n) for t, n, _c in _INDEXES if n in _indexes(t)]
    if pg:
        with op.get_context().autocommit_block():
            for table, name in todo:
                op.drop_index(name, table_name=table, postgresql_concurrently=True, if_exists=True)
    else:
        for table, name in todo:
            op.drop_index(name, table_name=table)
