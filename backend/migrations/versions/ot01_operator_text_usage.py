"""Daily count of texts each shop sends through the operator's shared Twilio account (H12).

One row per (shop, shop-local day); the send path bumps `sent` with a conditional upsert so the
cap holds across workers and restarts. Downgrade drops the table (the counts are only for today).

Revision ID: ot01
Revises: trg01
"""
import sqlalchemy as sa
from alembic import op

revision = "ot01"
down_revision = "trg01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operator_text_usage",
        sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id"), primary_key=True),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("sent", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("operator_text_usage")
