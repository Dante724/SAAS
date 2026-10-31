"""API v2 booking API: appointments.updated_at, for GET /api/v2/appointments?updated_since=.

Additive: a nullable column, backfilled from created_at.

Revision ID: bk1
Revises: wsc1
"""
import sqlalchemy as sa
from alembic import op

revision = "bk1"
down_revision = "wsc1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("appointments") as b:
        b.add_column(sa.Column("updated_at", sa.DateTime(), nullable=True))
    op.execute("UPDATE appointments SET updated_at = created_at WHERE updated_at IS NULL")


def downgrade() -> None:
    with op.batch_alter_table("appointments") as b:
        b.drop_column("updated_at")
