"""Build 144ak — intake (condition) photos on a work order.

Adds a JSON ``intake_photos`` column to ``orders`` holding a list of media blob ids that
document the device's condition at check-in (dispute protection). Dev (SQLite) adds it via
create_all/_sync_columns; this migration owns it for production.

Revision ID: c9e1g3i5k7m9
Revises: b8d0f2h4j6k8
"""
import sqlalchemy as sa
from alembic import op

revision = "ip144ak01"
down_revision = "ml144aj01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("intake_photos", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("orders", "intake_photos")
