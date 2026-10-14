"""Build 144ag — per-contact and per-asset "watch-out" warnings.

Adds a free-text ``watch_out`` column to ``clients`` and ``devices``. Surfaced as a
prominent banner on a work order so the front desk sees handling warnings
("battery swollen", "always disputes charges") before touching the job. Nullable,
no backfill needed. Dev (SQLite) adds this automatically via _sync_columns; this
migration owns it for production.

Revision ID: a7c9e1g3i5k7
Revises: z6b8d0f2h4j6
"""
import sqlalchemy as sa
from alembic import op

revision = "wa144ag01"
down_revision = "nd144bp01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clients", sa.Column("watch_out", sa.Text(), nullable=True))
    op.add_column("devices", sa.Column("watch_out", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("devices", "watch_out")
    op.drop_column("clients", "watch_out")
