"""b144ba — client_msg_unread on orders (customer messages)

Revision ID: e9a1c3d5f7b0
Revises: d8f0a2c4e6b9
Create Date: 2026-08-16
"""
from alembic import op
import sqlalchemy as sa

revision = "e9a1c3d5f7b0"
down_revision = "d8f0a2c4e6b9"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("orders") as batch_op:
        batch_op.add_column(sa.Column("client_msg_unread", sa.Integer(), nullable=False, server_default="0"))


def downgrade():
    with op.batch_alter_table("orders") as batch_op:
        batch_op.drop_column("client_msg_unread")
