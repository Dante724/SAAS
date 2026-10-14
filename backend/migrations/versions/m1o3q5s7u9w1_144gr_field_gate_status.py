"""144gr — field-gates-status on custom fields.

Revision ID: m1o3q5s7u9w1
Revises: l0n2p4r6t8v0
"""
from alembic import op
import sqlalchemy as sa

revision = "m1o3q5s7u9w1"
down_revision = "l0n2p4r6t8v0"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("custom_fields", sa.Column("gate_status_key", sa.String(length=50), nullable=True))
    op.add_column("custom_fields", sa.Column("gate_comparator", sa.String(length=6), nullable=True))
    op.add_column("custom_fields", sa.Column("gate_value", sa.String(length=200), nullable=True))
    op.add_column("custom_fields", sa.Column("gate_threshold", sa.Float(), nullable=True))


def downgrade():
    for c in ("gate_threshold", "gate_value", "gate_comparator", "gate_status_key"):
        op.drop_column("custom_fields", c)
