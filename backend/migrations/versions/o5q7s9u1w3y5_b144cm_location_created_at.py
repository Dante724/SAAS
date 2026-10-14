"""b144cm — locations get created_at for stable ordering

Revision ID: o5q7s9u1w3y5
Revises: n4p6r8t0v2x4
Create Date: 2026-08-21
"""
from alembic import op
import sqlalchemy as sa

revision = "o5q7s9u1w3y5"
down_revision = "n4p6r8t0v2x4"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("locations") as b:
        b.add_column(sa.Column("created_at", sa.DateTime(), nullable=True,
                               server_default=sa.func.now()))


def downgrade():
    with op.batch_alter_table("locations") as b:
        b.drop_column("created_at")
