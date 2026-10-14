"""144go — smart checklist upsell: linked catalog + threshold on checklist items,
source attribution on recommendations.

Revision ID: h6j8l0n2p4r6
Revises: g5i7k9m1o3q5
"""
from alembic import op
import sqlalchemy as sa

revision = "h6j8l0n2p4r6"
down_revision = "g5i7k9m1o3q5"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("checklist_template_items", "order_checklist_items"):
        op.add_column(table, sa.Column("upsell_ref_id", sa.String(), nullable=True))
        op.add_column(table, sa.Column("upsell_kind", sa.String(length=20), nullable=True))
        op.add_column(table, sa.Column("upsell_price", sa.Float(), nullable=True))
        op.add_column(table, sa.Column("upsell_threshold", sa.Float(), nullable=True))
        op.add_column(table, sa.Column("upsell_comparator", sa.String(length=4), nullable=True))
    op.add_column("recommendations", sa.Column("source", sa.String(length=20), nullable=False, server_default="manual"))
    op.add_column("recommendations", sa.Column("source_ref_id", sa.String(), nullable=True))


def downgrade():
    for table in ("checklist_template_items", "order_checklist_items"):
        for c in ("upsell_ref_id", "upsell_kind", "upsell_price", "upsell_threshold", "upsell_comparator"):
            op.drop_column(table, c)
    op.drop_column("recommendations", "source_ref_id")
    op.drop_column("recommendations", "source")
