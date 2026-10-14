"""144hb — promotional window on price types (starts_at / ends_at)."""
from alembic import op
import sqlalchemy as sa

revision = "p4r6t8v0x2z4"
down_revision = "o3q5s7u9w1y3"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("price_types") as b:
        b.add_column(sa.Column("starts_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("ends_at", sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table("price_types") as b:
        b.drop_column("ends_at")
        b.drop_column("starts_at")
