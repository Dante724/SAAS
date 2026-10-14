"""build 144ao — category template scoping

Revision ID: e3a5c7b9d1f4
Revises: d2f4a6b8c0e1
Create Date: 2026-08-13

Adds template_key to categories so each industry template can have its own tree.
"""
import sqlalchemy as sa
from alembic import op

revision = "e3a5c7b9d1f4"
down_revision = "d2f4a6b8c0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("categories") as batch:
        batch.add_column(sa.Column("template_key", sa.String(length=30), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("categories") as batch:
        batch.drop_column("template_key")
