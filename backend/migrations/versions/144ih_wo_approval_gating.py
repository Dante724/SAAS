"""144ih — public accept/decline gating per doc type (estimate + work order)."""
from alembic import op
import sqlalchemy as sa

revision = "z4b6d8f0h2j4"
down_revision = "y3a5c7e9g1i3"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("organisations", sa.Column("oe_wo_allow_approval", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("organisations", sa.Column("oe_approval_statuses", sa.String(length=500), nullable=True))
    op.add_column("organisations", sa.Column("oe_wo_approval_statuses", sa.String(length=500), nullable=True))


def downgrade():
    for c in ("oe_wo_approval_statuses", "oe_approval_statuses", "oe_wo_allow_approval"):
        op.drop_column("organisations", c)
