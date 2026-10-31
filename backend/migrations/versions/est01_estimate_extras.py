"""Estimate extras (E5): valid-until date, revisions with history, decline reason.

- orders.estimate_valid_until: after it the customer can't approve online.
- orders.estimate_revision: the version the customer sees (starts at 1).
- orders.estimate_decline_reason: what the customer wrote when declining.
- document_signatures.revision: which version a decision was for, so a new version can be decided.
- estimate_revisions: snapshots of earlier versions (lines, total, decision).

Revision ID: est01
Revises: im01
"""
import sqlalchemy as sa
from alembic import op

revision = "est01"
down_revision = "im01"
branch_labels = None
depends_on = None


def _cols(table: str) -> set:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    have = _cols("orders")
    if "estimate_valid_until" not in have:
        op.add_column("orders", sa.Column("estimate_valid_until", sa.Date(), nullable=True))
    if "estimate_revision" not in have:
        op.add_column("orders", sa.Column("estimate_revision", sa.Integer(), nullable=False, server_default="1"))
    if "estimate_decline_reason" not in have:
        op.add_column("orders", sa.Column("estimate_decline_reason", sa.Text(), nullable=True))
    if "revision" not in _cols("document_signatures"):
        op.add_column("document_signatures", sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
    if "estimate_revisions" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "estimate_revisions",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id"), nullable=False),
            sa.Column("order_id", sa.String(), sa.ForeignKey("orders.id"), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("lines", sa.JSON(), nullable=True),
            sa.Column("total", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("valid_until", sa.Date(), nullable=True),
            sa.Column("deposit_amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("decision", sa.String(20), nullable=True),
            sa.Column("decline_reason", sa.Text(), nullable=True),
            sa.Column("skipped", sa.JSON(), nullable=True),
            sa.Column("note", sa.String(500), nullable=True),
            sa.Column("created_by_id", sa.String(), sa.ForeignKey("employees.id"), nullable=True),
            sa.Column("created_by_name", sa.String(200), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_estimate_revisions_org_id", "estimate_revisions", ["org_id"])
        op.create_index("ix_estimate_revisions_order_id", "estimate_revisions", ["order_id"])


def downgrade() -> None:
    op.drop_index("ix_estimate_revisions_order_id", table_name="estimate_revisions")
    op.drop_index("ix_estimate_revisions_org_id", table_name="estimate_revisions")
    op.drop_table("estimate_revisions")
    op.drop_column("document_signatures", "revision")
    op.drop_column("orders", "estimate_decline_reason")
    op.drop_column("orders", "estimate_revision")
    op.drop_column("orders", "estimate_valid_until")
