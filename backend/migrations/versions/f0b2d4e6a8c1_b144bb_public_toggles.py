"""b144bb — public-page feature toggles (approval, messages)

Revision ID: f0b2d4e6a8c1
Revises: e9a1c3d5f7b0
Create Date: 2026-08-16
"""
from alembic import op
import sqlalchemy as sa

revision = "f0b2d4e6a8c1"
down_revision = "e9a1c3d5f7b0"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("organisations") as batch_op:
        batch_op.add_column(sa.Column("oe_allow_approval", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch_op.add_column(sa.Column("oe_allow_messages", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade():
    with op.batch_alter_table("organisations") as batch_op:
        batch_op.drop_column("oe_allow_messages")
        batch_op.drop_column("oe_allow_approval")
