"""Build 144bh — transactions.shift_id (manual cash income/expense feeds the drawer's expected cash).

Revision ID: tx144bh01
Revises: pp144bf01
"""
import sqlalchemy as sa
from alembic import op

revision = "tx144bh01"
down_revision = "pp144bf01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("transactions") as batch:
        batch.add_column(sa.Column("shift_id", sa.String(), sa.ForeignKey("cash_shifts.id"), nullable=True))
    op.create_index("ix_transactions_shift_id", "transactions", ["shift_id"])


def downgrade() -> None:
    op.drop_index("ix_transactions_shift_id", table_name="transactions")
    with op.batch_alter_table("transactions") as batch:
        batch.drop_column("shift_id")
