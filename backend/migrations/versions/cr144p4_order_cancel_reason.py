"""Phase 4 — record why a work order was cancelled.

Adds orders.cancel_reason (a Cancellation-reasons directory value captured when an order moves
into a dropped status). Nullable, no backfill.

Revision ID: cr144p4
Revises: ix144p4
"""
import sqlalchemy as sa
from alembic import op

revision = "cr144p4"
down_revision = "ix144p4"
branch_labels = None
depends_on = None


def _cols(table: str) -> set:
    insp = sa.inspect(op.get_bind())
    return {c["name"] for c in insp.get_columns(table)} if insp.has_table(table) else set()


def upgrade() -> None:
    if "cancel_reason" not in _cols("orders"):
        with op.batch_alter_table("orders") as b:
            b.add_column(sa.Column("cancel_reason", sa.String(120), nullable=True))


def downgrade() -> None:
    if "cancel_reason" in _cols("orders"):
        with op.batch_alter_table("orders") as b:
            b.drop_column("cancel_reason")
