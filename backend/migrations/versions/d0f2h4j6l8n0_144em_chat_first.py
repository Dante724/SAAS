"""144em: notification_rules.chat_first (try client's latest chat first)

Revision ID: d0f2h4j6l8n0
Revises: c9e1g3i5k7m9
Create Date: 2026-09-09
"""
from alembic import op
import sqlalchemy as sa

revision = "d0f2h4j6l8n0"
down_revision = "c9e1g3i5k7m9"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "notification_rules",
        sa.Column("chat_first", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade():
    op.drop_column("notification_rules", "chat_first")
