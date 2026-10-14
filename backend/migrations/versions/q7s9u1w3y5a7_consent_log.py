"""b144cs — GDPR consent log (proof-of-consent trail)

Revision ID: q7s9u1w3y5a7
Revises: p6r8t0v2x4z6
Create Date: 2026-08-26
"""
from alembic import op
import sqlalchemy as sa

revision = "q7s9u1w3y5a7"
down_revision = "p6r8t0v2x4z6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "consent_log",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id"), index=True),
        sa.Column("client_id", sa.String(), sa.ForeignKey("clients.id"), index=True),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("granted", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(length=60), nullable=True),
        sa.Column("actor_id", sa.String(), sa.ForeignKey("employees.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table("consent_log")
