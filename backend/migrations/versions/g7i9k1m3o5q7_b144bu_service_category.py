"""b144bu — services get a category_id FK to the shared category tree

Revision ID: g7i9k1m3o5q7
Revises: f6h8j0l2n4p6
Create Date: 2026-08-19
"""
from alembic import op
import sqlalchemy as sa

revision = "g7i9k1m3o5q7"
down_revision = "f6h8j0l2n4p6"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("services") as batch_op:
        batch_op.add_column(sa.Column("category_id", sa.String(), nullable=True))
        batch_op.create_index("ix_services_category_id", ["category_id"])


def downgrade():
    with op.batch_alter_table("services") as batch_op:
        batch_op.drop_index("ix_services_category_id")
        batch_op.drop_column("category_id")
