"""144ij — generalise OrderStatus to record_type (5 Orderry pipelines).

Adds order_statuses.record_type and backfills it from the legacy is_estimate
boolean: estimate where is_estimate, else work_order. The 3 new pipelines
(inquiry / invoice / transfer) are seeded lazily by statuses.ensure_seeded.

Revision ID: b6d8f0h2j4l6
Revises: a5c7e9g1i3k5
"""
from alembic import op
import sqlalchemy as sa

revision = "b6d8f0h2j4l6"
down_revision = "a5c7e9g1i3k5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "order_statuses",
        sa.Column("record_type", sa.String(length=20), nullable=False, server_default="work_order"),
    )
    # `WHERE is_estimate` is truthy-portable across sqlite (int) and postgres (bool).
    op.execute("UPDATE order_statuses SET record_type = 'estimate' WHERE is_estimate")


def downgrade() -> None:
    op.drop_column("order_statuses", "record_type")
