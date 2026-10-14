"""144hd — org discount-approval threshold."""
from alembic import op
import sqlalchemy as sa

revision = "q5s7u9w1y3a5"
down_revision = "p4r6t8v0x2z4"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("organisations") as b:
        b.add_column(sa.Column("discount_approval_pct", sa.Float(), nullable=False, server_default="0"))


def downgrade():
    with op.batch_alter_table("organisations") as b:
        b.drop_column("discount_approval_pct")
