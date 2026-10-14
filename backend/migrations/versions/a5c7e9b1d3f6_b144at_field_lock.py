"""b144at — lock_after_create on custom_fields

Revision ID: a5c7e9b1d3f6
Revises: f4b6d8c0e2a7
Create Date: 2026-08-14
"""
from alembic import op
import sqlalchemy as sa

revision = "a5c7e9b1d3f6"
down_revision = "f4b6d8c0e2a7"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("custom_fields") as batch_op:
        batch_op.add_column(sa.Column("lock_after_create", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    with op.batch_alter_table("custom_fields") as batch_op:
        batch_op.drop_column("lock_after_create")
