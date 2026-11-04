"""Customer notifications become a real outbox (architecture hardening: notification delivery).

notification_logs gets the delivery bookkeeping the sweep needs: how many tries so far
(attempts), when the next retry is due (next_attempt_at), the last provider error (last_error,
short) and when a sweep claimed the row (claimed_at, so a row stuck in "sending" can be
reclaimed). Plus an index on (status, send_at) for the sweep's "what is due" query.

Existing rows keep their status. Old "failed" rows stay "failed" (they are history, not retried);
old rows left in "sending" have no claimed_at and are never re-sent, as before.

Revision ID: nob01
Revises: ce144w3
"""
import sqlalchemy as sa
from alembic import op

revision = "nob01"
down_revision = "ce144w3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("notification_logs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("next_attempt_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("last_error", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("claimed_at", sa.DateTime(), nullable=True))
        batch_op.create_index("ix_notification_logs_status_send_at", ["status", "send_at"], unique=False)


def downgrade() -> None:
    # The old code knows "failed", not "dead".
    op.execute("UPDATE notification_logs SET status = 'failed' WHERE status = 'dead'")
    with op.batch_alter_table("notification_logs", schema=None) as batch_op:
        batch_op.drop_index("ix_notification_logs_status_send_at")
        batch_op.drop_column("claimed_at")
        batch_op.drop_column("last_error")
        batch_op.drop_column("next_attempt_at")
        batch_op.drop_column("attempts")
