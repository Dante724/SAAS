"""b144cd — org-wide mandatory 2FA policy

Revision ID: l2n4p6r8t0v2
Revises: k1m3o5q7s9u1
Create Date: 2026-08-20
"""
from alembic import op
import sqlalchemy as sa

revision = "l2n4p6r8t0v2"
down_revision = "k1m3o5q7s9u1"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("organisations") as b:
        b.add_column(sa.Column("require_mfa", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade():
    with op.batch_alter_table("organisations") as b:
        b.drop_column("require_mfa")
