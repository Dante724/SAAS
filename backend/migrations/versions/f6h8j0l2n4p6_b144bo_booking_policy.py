"""b144bo — booking availability policy: lead time, window, theme

Revision ID: f6h8j0l2n4p6
Revises: e5b7d9f1a3c5
Create Date: 2026-08-19
"""
from alembic import op
import sqlalchemy as sa

revision = "f6h8j0l2n4p6"
down_revision = "e5b7d9f1a3c5"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("organisations") as batch_op:
        batch_op.add_column(sa.Column("booking_lead_min", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("booking_window_days", sa.Integer(), nullable=False, server_default="30"))
        batch_op.add_column(sa.Column("booking_theme", sa.Integer(), nullable=False, server_default="1"))


def downgrade():
    with op.batch_alter_table("organisations") as batch_op:
        batch_op.drop_column("booking_theme")
        batch_op.drop_column("booking_window_days")
        batch_op.drop_column("booking_lead_min")
