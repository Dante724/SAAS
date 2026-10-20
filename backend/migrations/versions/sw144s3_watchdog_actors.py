"""Tekly Sense Watchdog — record who discounted a line, made a refund and closed a drawer.

Adds order_lines.discount_by_id, refunds.created_by_id and cash_shifts.closed_by_id (nullable;
old rows stay empty and simply aren't attributed). Filled by backend/db/actors.py.

Revision ID: sw144s3
Revises: ls144r4
"""
import sqlalchemy as sa
from alembic import op

revision = "sw144s3"
down_revision = "ls144r4"
branch_labels = None
depends_on = None

COLS = [("order_lines", "discount_by_id"), ("refunds", "created_by_id"), ("cash_shifts", "closed_by_id")]


def upgrade():
    for table, col in COLS:
        with op.batch_alter_table(table) as b:
            b.add_column(sa.Column(col, sa.String(), sa.ForeignKey("employees.id", name=f"fk_{table}_{col}"),
                                   nullable=True))
            b.create_index(f"ix_{table}_{col}", [col])


def downgrade():
    for table, col in COLS:
        with op.batch_alter_table(table) as b:
            b.drop_index(f"ix_{table}_{col}")
            b.drop_column(col)
