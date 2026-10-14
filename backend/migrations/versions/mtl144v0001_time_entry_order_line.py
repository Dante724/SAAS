"""Build 144v — TimeEntry.order_line_id (per-line labour attribution).

Lets several technicians each time their own labour/service line on one work
order, so hours and commission roll up per person + line instead of a blended
order total.

Revision ID: mtl144v0001
Revises: kit144r0002
"""
import sqlalchemy as sa
from alembic import op

revision = "mtl144v0001"
down_revision = "kit144r0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("time_entries") as b:
        b.add_column(sa.Column("order_line_id", sa.String(), sa.ForeignKey("order_lines.id"), nullable=True))
    op.create_index("ix_time_entries_order_line_id", "time_entries", ["order_line_id"])


def downgrade() -> None:
    op.drop_index("ix_time_entries_order_line_id", table_name="time_entries")
    with op.batch_alter_table("time_entries") as b:
        b.drop_column("order_line_id")
