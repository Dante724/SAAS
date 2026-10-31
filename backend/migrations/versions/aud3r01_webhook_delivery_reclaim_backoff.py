"""Webhook deliveries: reclaim interrupted sends, back off between retries (audit 29 Oct 2026, R3).

- claimed_at: when the current send started; "sending" long past it means the worker died.
- next_attempt_at: earliest time the retry sweep may try a failed delivery again.

Revision ID: aud3r01
Revises: aud10m01
"""

revision = "aud3r01"
down_revision = "aud10m01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Superseded in the merged launch line by whh1 (webhook hardening), which adds the same columns (webhook_deliveries.claimed_at / next_attempt_at).
    # Kept as a no-op so the revision chain (and any database already stamped here) stays valid.
    pass


def downgrade() -> None:
    pass
