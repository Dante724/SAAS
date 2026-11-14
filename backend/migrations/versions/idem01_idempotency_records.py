"""Idempotency keys: one row per (org, Idempotency-Key) so a retried payment or create runs once.

The same table is created by gc144g1 on the preview line (Tekly Go). This migration creates it only
when it isn't there yet, so the two lines can meet without a duplicate-table error.

Revision ID: idem01
Revises: mdec01
"""
import sqlalchemy as sa
from alembic import op

revision = "idem01"
down_revision = "mdec01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("idempotency_records"):
        return
    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id"), nullable=False),
        sa.Column("key", sa.String(length=80), nullable=False),
        sa.Column("endpoint", sa.String(length=80), nullable=False),
        sa.Column("response", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("org_id", "key", name="uq_idempotency_org_key"),
    )
    op.create_index("ix_idempotency_records_org_id", "idempotency_records", ["org_id"])
    op.create_index("ix_idempotency_records_created_at", "idempotency_records", ["created_at"])


def downgrade() -> None:
    # gc144g1 (the other line) creates and drops the same table: on `downgrade base` whichever of
    # the two runs second finds it already gone (final review P3-1: that used to fail mid-way).
    if not sa.inspect(op.get_bind()).has_table("idempotency_records"):
        return
    op.execute("DROP INDEX IF EXISTS ix_idempotency_records_created_at")
    op.execute("DROP INDEX IF EXISTS ix_idempotency_records_org_id")
    op.drop_table("idempotency_records")
