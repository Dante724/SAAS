"""b144cb — auth tokens (password reset + email verification) + email_verified

Revision ID: j0l2n4p6r8t0
Revises: i9k1m3o5q7s9
Create Date: 2026-08-20
"""
from alembic import op
import sqlalchemy as sa

revision = "j0l2n4p6r8t0"
down_revision = "i9k1m3o5q7s9"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "auth_tokens",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("employee_id", sa.String(), sa.ForeignKey("employees.id"), nullable=False, index=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False, index=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    with op.batch_alter_table("employees") as b:
        b.add_column(sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    with op.batch_alter_table("employees") as b:
        b.drop_column("email_verified")
    op.drop_table("auth_tokens")
