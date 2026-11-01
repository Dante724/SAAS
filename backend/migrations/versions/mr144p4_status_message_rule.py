"""Phase 4 — per-status customer messaging rule.

Adds order_statuses.allow_messages (nullable: unset = automatic, open statuses allow messages)
and organisations.oe_msg_days_after_close (messages stay open N days after an order closes,
default 14). No backfill: existing statuses stay automatic.

Revision ID: mr144p4
Revises: so144p4
"""
import sqlalchemy as sa
from alembic import op

revision = "mr144p4"
down_revision = "so144p4"
branch_labels = None
depends_on = None


def _cols(table: str) -> set:
    insp = sa.inspect(op.get_bind())
    return {c["name"] for c in insp.get_columns(table)} if insp.has_table(table) else set()


def upgrade() -> None:
    if "allow_messages" not in _cols("order_statuses"):
        with op.batch_alter_table("order_statuses") as b:
            b.add_column(sa.Column("allow_messages", sa.Boolean(), nullable=True))
    if "oe_msg_days_after_close" not in _cols("organisations"):
        with op.batch_alter_table("organisations") as b:
            b.add_column(sa.Column("oe_msg_days_after_close", sa.Integer(), nullable=False,
                                   server_default="14"))


def downgrade() -> None:
    if "oe_msg_days_after_close" in _cols("organisations"):
        with op.batch_alter_table("organisations") as b:
            b.drop_column("oe_msg_days_after_close")
    if "allow_messages" in _cols("order_statuses"):
        with op.batch_alter_table("order_statuses") as b:
            b.drop_column("allow_messages")
