"""144gy — org margin-floor guardrail settings.

Adds Organisation.min_margin_pct + margin_guard so a shop can warn or block work-order
lines discounted below a minimum margin.
"""
from alembic import op
import sqlalchemy as sa

revision = "o3q5s7u9w1y3"
down_revision = "n2p4r6t8v0x2"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("organisations") as b:
        b.add_column(sa.Column("min_margin_pct", sa.Float(), nullable=False, server_default="0"))
        b.add_column(sa.Column("margin_guard", sa.String(), nullable=False, server_default="off"))


def downgrade():
    with op.batch_alter_table("organisations") as b:
        b.drop_column("margin_guard")
        b.drop_column("min_margin_pct")
