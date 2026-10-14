"""144he — quantity-break tiers on price types."""
from alembic import op
import sqlalchemy as sa

revision = "r6t8v0x2z4b6"
down_revision = "q5s7u9w1y3a5"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "price_type_tiers",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id"), index=True),
        sa.Column("price_type_id", sa.String(), sa.ForeignKey("price_types.id"), index=True),
        sa.Column("min_qty", sa.Float(), nullable=False, server_default="1"),
        sa.Column("markup_pct", sa.Float(), nullable=False, server_default="0"),
    )


def downgrade():
    op.drop_table("price_type_tiers")
