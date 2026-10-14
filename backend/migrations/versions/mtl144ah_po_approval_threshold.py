"""144ah — organisations.po_approval_threshold (PO spend-approval gate)

Revision ID: mtl144ah001
Revises: mtl144aac001
Create Date: 2026-09-28
"""
import sqlalchemy as sa
from alembic import op

revision = "mtl144ah001"
down_revision = "mtl144aac001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("organisations", sa.Column("po_approval_threshold", sa.Float(), nullable=True))


def downgrade():
    op.drop_column("organisations", "po_approval_threshold")
