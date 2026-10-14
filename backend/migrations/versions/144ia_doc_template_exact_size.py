"""144ia — exact physical print size on document templates."""
from alembic import op
import sqlalchemy as sa

revision = "w1y3a5c7e9g1"
down_revision = "v0x2z4b6d8f0"
branch_labels = None
depends_on = None

_COLS = ["page_w_mm", "page_h_mm", "margin_mm"]


def upgrade():
    for c in _COLS:
        op.add_column("document_templates", sa.Column(c, sa.Float(), nullable=True))


def downgrade():
    for c in reversed(_COLS):
        op.drop_column("document_templates", c)
