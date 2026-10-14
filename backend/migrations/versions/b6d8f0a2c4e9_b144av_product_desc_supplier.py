"""b144av — product description + default supplier

Revision ID: b6d8f0a2c4e9
Revises: a5c7e9b1d3f6
Create Date: 2026-08-14
"""
from alembic import op
import sqlalchemy as sa

revision = "b6d8f0a2c4e9"
down_revision = "a5c7e9b1d3f6"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("parts") as batch_op:
        batch_op.add_column(sa.Column("description", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("default_supplier_id", sa.String(), nullable=True))


def downgrade():
    with op.batch_alter_table("parts") as batch_op:
        batch_op.drop_column("default_supplier_id")
        batch_op.drop_column("description")
