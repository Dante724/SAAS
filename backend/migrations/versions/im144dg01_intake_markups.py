"""Build 144dg — orders.intake_markups ({original_blob_id: marked_blob_id}). A marked-up copy of an
intake photo keeps the untouched original on file for disputes.

Revision ID: im144dg01
Revises: jk144df01
"""
import sqlalchemy as sa
from alembic import op

revision = "im144dg01"
down_revision = "jk144df01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("orders") as batch:
        batch.add_column(sa.Column("intake_markups", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("orders") as batch:
        batch.drop_column("intake_markups")
