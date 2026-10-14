"""Build 144bf — processor_payouts table (card/online deposit reconciliation, 3rd leg).

Revision ID: pp144bf01
Revises: dr144bc01
"""
import sqlalchemy as sa
from alembic import op

revision = "pp144bf01"
down_revision = "dr144bc01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "processor_payouts",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id"), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False, server_default="manual"),
        sa.Column("payout_date", sa.Date(), nullable=True),
        sa.Column("gross_amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("fee_amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("net_amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("reference", sa.String(length=120), nullable=True),
        sa.Column("note", sa.String(length=300), nullable=True),
        sa.Column("created_by_id", sa.String(), sa.ForeignKey("employees.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_processor_payouts_org_id", "processor_payouts", ["org_id"])


def downgrade() -> None:
    op.drop_table("processor_payouts")
