"""144az — time-entry dispute flag (tech "My Work" + dispute flow).

Revision ID: mtl144az001
Revises: mtl144ay001
Create Date: 2026-10-01
"""
from alembic import op
import sqlalchemy as sa

revision = "mtl144az001"
down_revision = "mtl144ay001"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("time_entries") as b:
        b.add_column(sa.Column("disputed", sa.Boolean(), nullable=False, server_default=sa.false()))
        b.add_column(sa.Column("dispute_note", sa.Text()))


def downgrade():
    with op.batch_alter_table("time_entries") as b:
        b.drop_column("dispute_note")
        b.drop_column("disputed")
