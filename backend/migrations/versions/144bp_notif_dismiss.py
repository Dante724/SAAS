"""144bp — StaffNotification.dismissed_at (soft-dismiss from the bell)

Revision ID: nd144bp01
Revises: lsa144bo01
Create Date: 2026-10-02
"""
from alembic import op
import sqlalchemy as sa

revision = "nd144bp01"
down_revision = "lsa144bo01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("staff_notifications") as b:
        b.add_column(sa.Column("dismissed_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("staff_notifications") as b:
        b.drop_column("dismissed_at")
