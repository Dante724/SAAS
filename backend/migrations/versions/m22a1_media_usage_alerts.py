"""PERF-22 (owner decision 4): media_usage_alerts, the platform admins' private alert when one
shop's stored media passes MEDIA_ALERT_BYTES (default 25 GB). Shops never see it; nothing is capped.

One open alert per shop: uq_media_usage_alerts_open, partial unique on cleared_at IS NULL, so two
workers sweeping at once can't both open one.

Additive only: a new, empty table. Downgrade drops it (the alerts are recomputed by the next sweep).

Revision ID: m22a1
Revises: w4s01
"""
import sqlalchemy as sa
from alembic import op

revision = "m22a1"
down_revision = "w4s01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "media_usage_alerts",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id"), nullable=False),
        sa.Column("bytes", sa.BigInteger(), nullable=False),
        sa.Column("threshold_bytes", sa.BigInteger(), nullable=False),
        sa.Column("crossed_at", sa.DateTime(), nullable=False),
        sa.Column("cleared_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_media_usage_alerts_org_id", "media_usage_alerts", ["org_id"])
    op.create_index("uq_media_usage_alerts_open", "media_usage_alerts", ["org_id"], unique=True,
                    postgresql_where=sa.text("cleared_at IS NULL"), sqlite_where=sa.text("cleared_at IS NULL"))


def downgrade() -> None:
    op.drop_index("uq_media_usage_alerts_open", table_name="media_usage_alerts")
    op.drop_index("ix_media_usage_alerts_org_id", table_name="media_usage_alerts")
    op.drop_table("media_usage_alerts")
