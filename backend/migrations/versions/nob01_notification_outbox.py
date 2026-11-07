"""Customer notifications become a real outbox (architecture hardening: notification delivery).

notification_logs gets the delivery bookkeeping the sweep needs: how many tries so far
(attempts), when the next retry is due (next_attempt_at), the last provider error (last_error,
short) and when a sweep claimed the row (claimed_at, so a row stuck in "sending" can be
reclaimed). Plus an index on (status, send_at) for the sweep's "what is due" query.

Every status change now inserts into notification_logs, so on Postgres the index is built
CONCURRENTLY (no write lock on the table while it builds), outside the migration transaction.

Existing rows keep their status. Old "failed" rows stay "failed" (they are history, not retried);
old rows left in "sending" have no claimed_at and are never re-sent, as before.

Revision ID: nob01
Revises: trg01
"""
import sqlalchemy as sa
from alembic import op

revision = "nob01"
down_revision = "trg01"
branch_labels = None
depends_on = None

INDEX = "ix_notification_logs_status_send_at"


def upgrade() -> None:
    with op.batch_alter_table("notification_logs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("next_attempt_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("last_error", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("claimed_at", sa.DateTime(), nullable=True))
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.create_index(INDEX, "notification_logs", ["status", "send_at"], unique=False,
                            postgresql_concurrently=True, if_not_exists=True)
    else:
        op.create_index(INDEX, "notification_logs", ["status", "send_at"], unique=False)


def downgrade() -> None:
    # The old code knows "failed", not "dead".
    op.execute("UPDATE notification_logs SET status = 'failed' WHERE status = 'dead'")
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.drop_index(INDEX, table_name="notification_logs", postgresql_concurrently=True,
                          if_exists=True)
    else:
        op.drop_index(INDEX, table_name="notification_logs")
    with op.batch_alter_table("notification_logs", schema=None) as batch_op:
        batch_op.drop_column("claimed_at")
        batch_op.drop_column("last_error")
        batch_op.drop_column("next_attempt_at")
        batch_op.drop_column("attempts")
