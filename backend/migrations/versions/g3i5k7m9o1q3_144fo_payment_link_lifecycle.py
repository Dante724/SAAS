"""144fo: payment-link lifecycle — viewed_at on payment_intents

Adds a viewed_at timestamp so the pay link has a visible Created→Viewed→
Waiting→Paid/Failed/Revoked lifecycle (Orderry parity). Additive.

Revision ID: g3i5k7m9o1q3
Revises: f2h4j6l8n0p2
Create Date: 2026-09-17
"""
from alembic import op
import sqlalchemy as sa

revision = "g3i5k7m9o1q3"
down_revision = "f2h4j6l8n0p2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("payment_intents", sa.Column("viewed_at", sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column("payment_intents", "viewed_at")
