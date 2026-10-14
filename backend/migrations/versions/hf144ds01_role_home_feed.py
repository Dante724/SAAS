"""144ds — Role.home_feed_sources: owner picks which Home-feed signals each role sees.

Nullable JSON list of signal keys (overdue, messages, ready, urgent, unpaid, lowstock,
tasks, inquiries). NULL or empty means "all" — so existing roles are unchanged.

Revision ID: hf144ds01
Revises: ix144dh01
Create Date: 2026-10-12
"""
from alembic import op
import sqlalchemy as sa

revision = "hf144ds01"
down_revision = "ix144dh01"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("roles") as b:
        b.add_column(sa.Column("home_feed_sources", sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table("roles") as b:
        b.drop_column("home_feed_sources")
