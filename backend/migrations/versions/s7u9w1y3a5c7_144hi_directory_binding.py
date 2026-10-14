"""144hi — directory→field binding + per-location directory items."""
from alembic import op
import sqlalchemy as sa

revision = "s7u9w1y3a5c7"
down_revision = "r6t8v0x2z4b6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("custom_fields", sa.Column("directory_key", sa.String(), nullable=True))
    op.add_column("directory_items", sa.Column("location_id", sa.String(), nullable=True))


def downgrade():
    op.drop_column("directory_items", "location_id")
    op.drop_column("custom_fields", "directory_key")
