"""144hj — cascading directory fields (custom_fields.parent_field_key)."""
from alembic import op
import sqlalchemy as sa

revision = "t8v0x2z4b6d8"
down_revision = "s7u9w1y3a5c7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("custom_fields", sa.Column("parent_field_key", sa.String(), nullable=True))


def downgrade():
    op.drop_column("custom_fields", "parent_field_key")
