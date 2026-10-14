"""144gp Phase B — custom-field upsell rule (unified rule surface).

Revision ID: j8l0n2p4r6t8
Revises: i7k9m1o3q5s7
"""
from alembic import op
import sqlalchemy as sa

revision = "j8l0n2p4r6t8"
down_revision = "i7k9m1o3q5s7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("custom_fields", sa.Column("upsell_ref_id", sa.String(), nullable=True))
    op.add_column("custom_fields", sa.Column("upsell_kind", sa.String(length=20), nullable=True))
    op.add_column("custom_fields", sa.Column("upsell_price", sa.Float(), nullable=True))
    op.add_column("custom_fields", sa.Column("upsell_comparator", sa.String(length=6), nullable=True))
    op.add_column("custom_fields", sa.Column("upsell_value", sa.String(length=200), nullable=True))
    op.add_column("custom_fields", sa.Column("upsell_threshold", sa.Float(), nullable=True))


def downgrade():
    for c in ("upsell_threshold", "upsell_value", "upsell_comparator", "upsell_price", "upsell_kind", "upsell_ref_id"):
        op.drop_column("custom_fields", c)
