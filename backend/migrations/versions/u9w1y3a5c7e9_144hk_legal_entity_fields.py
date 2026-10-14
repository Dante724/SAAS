"""144hk — richer legal-entity print fields."""
from alembic import op
import sqlalchemy as sa

revision = "u9w1y3a5c7e9"
down_revision = "t8v0x2z4b6d8"
branch_labels = None
depends_on = None

_COLS = ["registration_no", "vat_no", "bank_name", "bank_account", "phone", "email", "director"]


def upgrade():
    for c in _COLS:
        op.add_column("legal_entities", sa.Column(c, sa.String(), nullable=True))


def downgrade():
    for c in reversed(_COLS):
        op.drop_column("legal_entities", c)
