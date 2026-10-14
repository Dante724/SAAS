"""b144bj — technician notes + conclusion on work orders

Revision ID: d4f6a8c0e2b4
Revises: c3e5b7d9f1a3
Create Date: 2026-08-17
"""
from alembic import op
import sqlalchemy as sa

revision = "d4f6a8c0e2b4"
down_revision = "c3e5b7d9f1a3"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("orders") as batch_op:
        batch_op.add_column(sa.Column("tech_notes", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("conclusion", sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table("orders") as batch_op:
        batch_op.drop_column("conclusion")
        batch_op.drop_column("tech_notes")
