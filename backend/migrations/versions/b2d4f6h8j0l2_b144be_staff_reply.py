"""b144be — staff reply toggle on public pages

Revision ID: b2d4f6a8c0e2
Revises: a1c3e5b7d9f1
Create Date: 2026-08-16
"""
from alembic import op
import sqlalchemy as sa

revision = "b2d4f6a8c0e2"
down_revision = "a1c3e5b7d9f1"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("organisations") as batch_op:
        batch_op.add_column(sa.Column("oe_allow_staff_reply", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade():
    with op.batch_alter_table("organisations") as batch_op:
        batch_op.drop_column("oe_allow_staff_reply")
