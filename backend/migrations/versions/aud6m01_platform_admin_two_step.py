"""Platform admin: 2-step sign-in and revocable tokens (audit 29 Oct 2026, M6).

- token_version: bumped to sign the admin out everywhere; tokens carry it as "tv".
- mfa_enabled / mfa_secret (encrypted with services/crypto) / mfa_last_step (replay guard).

Revision ID: aud6m01
Revises: mkw01
"""
import sqlalchemy as sa
from alembic import op

revision = "aud6m01"
down_revision = "mkw01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("platform_admins") as b:
        b.add_column(sa.Column("token_version", sa.Integer(), nullable=False, server_default=sa.text("0")))
        b.add_column(sa.Column("mfa_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")))
        b.add_column(sa.Column("mfa_secret", sa.String(255), nullable=True))
        b.add_column(sa.Column("mfa_last_step", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("platform_admins") as b:
        b.drop_column("mfa_last_step")
        b.drop_column("mfa_secret")
        b.drop_column("mfa_enabled")
        b.drop_column("token_version")
