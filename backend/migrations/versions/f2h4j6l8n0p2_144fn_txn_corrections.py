"""144fn: transaction corrections + soft-delete (append-only cash ledger)

Adds status/balance_after/reverses_txn_id/void_reason/voided_by_id to
transactions so money can be corrected (signed delta row) or soft-deleted
without ever mutating or erasing the original — matching Orderry's
strikethrough ledger. Additive + backfill; no data loss.

Revision ID: f2h4j6l8n0p2
Revises: e1g3i5k7m9o1
Create Date: 2026-09-17
"""
from alembic import op
import sqlalchemy as sa

revision = "f2h4j6l8n0p2"
down_revision = "e1g3i5k7m9o1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("transactions", sa.Column("status", sa.String(12), nullable=False, server_default="active"))
    op.add_column("transactions", sa.Column("balance_after", sa.Float(), nullable=True))
    op.add_column("transactions", sa.Column("reverses_txn_id", sa.String(), nullable=True))
    op.add_column("transactions", sa.Column("void_reason", sa.String(300), nullable=True))
    op.add_column("transactions", sa.Column("voided_by_id", sa.String(), nullable=True))
    # existing rows are all live
    op.execute("UPDATE transactions SET status='active' WHERE status IS NULL")


def downgrade():
    op.drop_column("transactions", "voided_by_id")
    op.drop_column("transactions", "void_reason")
    op.drop_column("transactions", "reverses_txn_id")
    op.drop_column("transactions", "balance_after")
    op.drop_column("transactions", "status")
