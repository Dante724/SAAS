"""b144ay — insight_cards (Company Insights)

Revision ID: d8f0a2c4e6b9
Revises: c7e9a1b3d5f8
Create Date: 2026-08-16
"""
from alembic import op
import sqlalchemy as sa

revision = "d8f0a2c4e6b9"
down_revision = "c7e9a1b3d5f8"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "insight_cards",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id"), index=True),
        sa.Column("kind", sa.String(length=12), server_default="diagram"),
        sa.Column("subject", sa.String(length=40)),
        sa.Column("metric", sa.String(length=40), server_default="amount"),
        sa.Column("group_by", sa.String(length=40), nullable=True),
        sa.Column("period", sa.String(length=20), server_default="30d"),
        sa.Column("view", sa.String(length=12), server_default="chart"),
        sa.Column("sort_order", sa.Integer(), server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_table("insight_cards")
