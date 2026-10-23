"""Signed-in devices and a per-login device limit.

- auth_sessions: one row per sign-in on one device (the token's `sid`). Lists a person's devices,
  lets them (or an admin) sign one out, and is what the shop's device limit counts.
- organisations.max_sessions_per_login: how many devices one login may use at once (null = no limit).

Revision ID: ds144a1
Revises: opd1
"""
import sqlalchemy as sa
from alembic import op

revision = "ds144a1"
down_revision = "opd1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("sid", sa.String(length=64), nullable=False),
        sa.Column("employee_id", sa.String(), nullable=False),
        sa.Column("org_id", sa.String(), nullable=False),
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("token_expires_at", sa.DateTime(), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("device", sa.String(length=80), nullable=True),
        sa.Column("via", sa.String(length=20), nullable=False, server_default="password"),
        sa.Column("mfa", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_by", sa.String(), nullable=True),
        sa.Column("revoke_reason", sa.String(length=20), nullable=True),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], name="fk_auth_sessions_employee_id_employees",
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_id"], ["organisations.id"], name="fk_auth_sessions_org_id_organisations",
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_auth_sessions"),
    )
    op.create_index("ix_auth_sessions_sid", "auth_sessions", ["sid"], unique=True)
    op.create_index("ix_auth_sessions_employee_id", "auth_sessions", ["employee_id"])
    op.create_index("ix_auth_sessions_org_id", "auth_sessions", ["org_id"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])
    with op.batch_alter_table("organisations") as b:
        b.add_column(sa.Column("max_sessions_per_login", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("organisations") as b:
        b.drop_column("max_sessions_per_login")
    op.drop_index("ix_auth_sessions_expires_at", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_org_id", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_employee_id", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_sid", table_name="auth_sessions")
    op.drop_table("auth_sessions")
