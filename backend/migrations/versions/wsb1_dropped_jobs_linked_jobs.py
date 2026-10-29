"""Workstream B — dropped jobs and linked jobs.

orders.written_off: what the customer still owed when the job was dropped, written off so it's no
longer a debt (0 for every existing job, so nothing changes until a job is dropped).
orders.continued_from_order_id: "Continue as a linked job" — the job a new one carries on from.
Additive; existing rows are untouched.

Revision ID: wsb1
Revises: opd1
"""
import sqlalchemy as sa
from alembic import op

revision = "wsb1"
down_revision = "opd1"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    # ojj1 (a sibling off opd1) adds the same column; whichever runs first adds it.
    if not _has_column("orders", "written_off"):
        with op.batch_alter_table("orders") as b:
            b.add_column(sa.Column("written_off", sa.Numeric(12, 2, asdecimal=False), nullable=False,
                                   server_default="0"))
    with op.batch_alter_table("orders") as b:
        b.add_column(sa.Column("continued_from_order_id", sa.String(), nullable=True))
        b.create_foreign_key("fk_orders_continued_from_order_id_orders", "orders",
                             ["continued_from_order_id"], ["id"], ondelete="SET NULL")
        b.create_index("ix_orders_continued_from_order_id", ["continued_from_order_id"])


def downgrade() -> None:
    with op.batch_alter_table("orders") as b:
        b.drop_index("ix_orders_continued_from_order_id")
        b.drop_constraint("fk_orders_continued_from_order_id_orders", type_="foreignkey")
        b.drop_column("continued_from_order_id")
        # written_off belongs to ojj1 too; ojj1's downgrade drops it.
