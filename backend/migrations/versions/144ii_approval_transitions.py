"""144ii — accept/decline target statuses + require-signature per doc type."""
from alembic import op
import sqlalchemy as sa

revision = "a5c7e9g1i3k5"
down_revision = "z4b6d8f0h2j4"
branch_labels = None
depends_on = None

_BOOLS = ["oe_estimate_require_signature", "oe_wo_require_signature"]
_STRS = ["oe_estimate_accept_status", "oe_estimate_decline_status",
         "oe_wo_accept_status", "oe_wo_decline_status"]


def upgrade():
    for c in _BOOLS:
        op.add_column("organisations", sa.Column(c, sa.Boolean(), nullable=False, server_default=sa.false()))
    for c in _STRS:
        op.add_column("organisations", sa.Column(c, sa.String(length=50), nullable=True))


def downgrade():
    for c in reversed(_STRS + _BOOLS):
        op.drop_column("organisations", c)
