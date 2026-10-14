"""144cp: order manager_id (WO owner/manager, Orderry parity)

Revision ID: b8d0f2h4j6l8
Revises: a7c9e1g3i5k7
Create Date: 2026-09-04

Adds an optional manager_id to orders — the work order's owner/manager, kept
distinct from assigned_to_id (the specialist doing the work). Additive and
nullable, so existing rows are unaffected.
"""
from alembic import op
import sqlalchemy as sa

revision = "b8d0f2h4j6l8"
down_revision = "a7c9e1g3i5k7"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("orders") as batch:
        batch.add_column(sa.Column("manager_id", sa.String(), nullable=True))


def downgrade():
    with op.batch_alter_table("orders") as batch:
        batch.drop_column("manager_id")
