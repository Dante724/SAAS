"""b144cc — teammate invitations

Revision ID: k1m3o5q7s9u1
Revises: j0l2n4p6r8t0
Create Date: 2026-08-20
"""
from alembic import op
import sqlalchemy as sa

revision = "k1m3o5q7s9u1"
down_revision = "j0l2n4p6r8t0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "invites",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id"), nullable=False, index=True),
        sa.Column("email", sa.String(length=200), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("role_id", sa.String(), sa.ForeignKey("roles.id"), nullable=True),
        sa.Column("location_id", sa.String(), sa.ForeignKey("locations.id"), nullable=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False, index=True),
        sa.Column("invited_by", sa.String(), sa.ForeignKey("employees.id"), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_table("invites")
