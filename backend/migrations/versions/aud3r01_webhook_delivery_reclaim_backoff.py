"""Webhook deliveries: reclaim interrupted sends, back off between retries (audit 29 Oct 2026, R3).

- claimed_at: when the current send started; "sending" long past it means the worker died.
- next_attempt_at: earliest time the retry sweep may try a failed delivery again.

Revision ID: aud3r01
Revises: aud10m01
"""
import sqlalchemy as sa
from alembic import op

revision = "aud3r01"
down_revision = "aud10m01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("webhook_deliveries") as b:
        b.add_column(sa.Column("claimed_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("next_attempt_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("webhook_deliveries") as b:
        b.drop_column("next_attempt_at")
        b.drop_column("claimed_at")
