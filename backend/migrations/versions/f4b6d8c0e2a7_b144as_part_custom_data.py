"""b144as — custom_data JSON on parts (product custom fields)

Revision ID: f4b6d8c0e2a7
Revises: e3a5c7b9d1f4
Create Date: 2026-08-14
"""
from alembic import op
import sqlalchemy as sa

revision = "f4b6d8c0e2a7"
down_revision = "e3a5c7b9d1f4"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("parts") as batch_op:
        batch_op.add_column(sa.Column("custom_data", sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table("parts") as batch_op:
        batch_op.drop_column("custom_data")
