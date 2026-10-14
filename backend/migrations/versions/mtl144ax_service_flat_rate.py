"""144ax — flat-rate labour flag on services (bill booked hours × rate).

Revision ID: mtl144ax001
Revises: mtl144av001
Create Date: 2026-10-01
"""
from alembic import op
import sqlalchemy as sa

revision = "mtl144ax001"
down_revision = "mtl144av001"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("services") as b:
        b.add_column(sa.Column("flat_rate", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    with op.batch_alter_table("services") as b:
        b.drop_column("flat_rate")
