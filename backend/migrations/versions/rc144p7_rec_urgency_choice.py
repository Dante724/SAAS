"""Recommendations the customer chooses: urgency, "not now", who confirmed.

Adds to recommendations:
  urgency          now | soon | null      — staff say how urgent it is (shown in plain words)
  decline_reason   not_now | not_needed | null — "not now" is deferred work the shop can raise again
  remind_after     date                   — set when the customer says "not now"
  decided_by_name  the name the customer typed when confirming
Additive and nullable; nothing reads them for older rows.

Revision ID: rc144p7
Revises: mb144p6
"""
import sqlalchemy as sa
from alembic import op

revision = "rc144p7"
down_revision = "mb144p6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("recommendations") as b:
        b.add_column(sa.Column("urgency", sa.String(10), nullable=True))
        b.add_column(sa.Column("decline_reason", sa.String(20), nullable=True))
        b.add_column(sa.Column("remind_after", sa.Date(), nullable=True))
        b.add_column(sa.Column("decided_by_name", sa.String(200), nullable=True))
        b.create_index("ix_recommendations_org_remind_after", ["org_id", "remind_after"])


def downgrade() -> None:
    with op.batch_alter_table("recommendations") as b:
        b.drop_index("ix_recommendations_org_remind_after")
        b.drop_column("decided_by_name")
        b.drop_column("remind_after")
        b.drop_column("decline_reason")
        b.drop_column("urgency")
