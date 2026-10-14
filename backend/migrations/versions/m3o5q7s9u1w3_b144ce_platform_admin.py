"""b144ce — platform-admin tier, subscription lifecycle, per-employee windows, coupons

Revision ID: m3o5q7s9u1w3
Revises: l2n4p6r8t0v2
Create Date: 2026-08-20
"""
from alembic import op
import sqlalchemy as sa

revision = "m3o5q7s9u1w3"
down_revision = "l2n4p6r8t0v2"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("organisations") as b:
        b.add_column(sa.Column("sub_status", sa.String(length=20), nullable=False, server_default="trial"))
        b.add_column(sa.Column("sub_start", sa.Date(), nullable=True))
        b.add_column(sa.Column("sub_end", sa.Date(), nullable=True))
        b.add_column(sa.Column("discount_pct", sa.Float(), nullable=False, server_default="0"))
        b.add_column(sa.Column("admin_notes", sa.Text(), nullable=True))
    with op.batch_alter_table("employees") as b:
        b.add_column(sa.Column("access_start", sa.Date(), nullable=True))
        b.add_column(sa.Column("access_end", sa.Date(), nullable=True))
    op.create_table(
        "platform_admins",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("email", sa.String(length=200), nullable=False, unique=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "platform_coupons",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("code", sa.String(length=40), nullable=False, unique=True, index=True),
        sa.Column("percent", sa.Float(), nullable=False, server_default="0"),
        sa.Column("plan", sa.String(length=50), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("expires_at", sa.Date(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_table("platform_coupons")
    op.drop_table("platform_admins")
    with op.batch_alter_table("employees") as b:
        b.drop_column("access_end")
        b.drop_column("access_start")
    with op.batch_alter_table("organisations") as b:
        b.drop_column("admin_notes")
        b.drop_column("discount_pct")
        b.drop_column("sub_end")
        b.drop_column("sub_start")
        b.drop_column("sub_status")
