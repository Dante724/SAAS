"""b144cr — multi-currency: currencies + rate history, account currency, transfer fx_rate

Revision ID: p6r8t0v2x4z6
Revises: o5q7s9u1w3y5
Create Date: 2026-08-26
"""
from alembic import op
import sqlalchemy as sa

revision = "p6r8t0v2x4z6"
down_revision = "o5q7s9u1w3y5"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "currencies",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id"), index=True),
        sa.Column("code", sa.String(length=8), nullable=False),
        sa.Column("symbol", sa.String(length=8), server_default=""),
        sa.Column("position", sa.String(length=10), server_default="before"),
        sa.Column("rate", sa.Float(), server_default="1.0"),
        sa.Column("active", sa.Boolean(), server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table(
        "currency_rate_history",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("currency_id", sa.String(), sa.ForeignKey("currencies.id"), index=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id")),
        sa.Column("rate", sa.Float(), nullable=False),
        sa.Column("changed_by", sa.String(), sa.ForeignKey("employees.id"), nullable=True),
        sa.Column("changed_at", sa.DateTime(), server_default=sa.func.now()),
    )
    with op.batch_alter_table("accounts") as b:
        b.add_column(sa.Column("currency_code", sa.String(length=8), nullable=True))
    with op.batch_alter_table("transactions") as b:
        b.add_column(sa.Column("fx_rate", sa.Float(), nullable=True))


def downgrade():
    with op.batch_alter_table("transactions") as b:
        b.drop_column("fx_rate")
    with op.batch_alter_table("accounts") as b:
        b.drop_column("currency_code")
    op.drop_table("currency_rate_history")
    op.drop_table("currencies")
