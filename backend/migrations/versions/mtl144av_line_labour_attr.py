"""144av — per-line labour: booked_hours + co_technicians split on order_lines.

Revision ID: mtl144av001
Revises: mtl144ar001
Create Date: 2026-10-01
"""
from alembic import op
import sqlalchemy as sa

revision = "mtl144av001"
down_revision = "mtl144ar001"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("order_lines") as b:
        b.add_column(sa.Column("booked_hours", sa.Float(), nullable=False, server_default="0"))
        b.add_column(sa.Column("co_technicians", sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table("order_lines") as b:
        b.drop_column("co_technicians")
        b.drop_column("booked_hours")
