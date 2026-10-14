"""144hl — client B2B billing fields for document variables."""
from alembic import op
import sqlalchemy as sa

revision = "v0x2z4b6d8f0"
down_revision = "u9w1y3a5c7e9"
branch_labels = None
depends_on = None

_COLS = ["tax_id", "reg_no", "billing_address"]


def upgrade():
    for c in _COLS:
        op.add_column("clients", sa.Column(c, sa.String(), nullable=True))


def downgrade():
    for c in reversed(_COLS):
        op.drop_column("clients", c)
