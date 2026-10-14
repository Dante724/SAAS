"""144fr: per-status VIEW ACL — roles.hidden_status_keys

A role can be denied visibility of orders/estimates in specific statuses
(deny-wins, default-allow). Complements the existing transition (set) ACL.
Additive; nullable JSON.

Revision ID: h4j6l8n0p2r4
Revises: g3i5k7m9o1q3
Create Date: 2026-09-18
"""
from alembic import op
import sqlalchemy as sa

revision = "h4j6l8n0p2r4"
down_revision = "g3i5k7m9o1q3"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("roles", sa.Column("hidden_status_keys", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("roles", "hidden_status_keys")
