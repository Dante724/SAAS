"""144bo — Part.low_stock_alerted (one-shot low-stock alert dedupe)

Revision ID: lsa144bo01
Revises: mtl144az001
Create Date: 2026-10-02
"""
from alembic import op
import sqlalchemy as sa

revision = "lsa144bo01"
down_revision = "mtl144az001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("parts") as b:
        b.add_column(sa.Column("low_stock_alerted", sa.Boolean(), nullable=False,
                               server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("parts") as b:
        b.drop_column("low_stock_alerted")
