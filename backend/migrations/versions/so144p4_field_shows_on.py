"""Forms step 2 — where a custom field shows: job card + customer portal (receipt = show_on_print).

Revision ID: so144p4
Revises: id144p4
"""
from alembic import op
import sqlalchemy as sa

revision = "so144p4"
down_revision = "id144p4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("custom_fields", sa.Column("show_on_job_card", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("custom_fields", sa.Column("show_in_portal", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column("custom_fields", "show_in_portal")
    op.drop_column("custom_fields", "show_on_job_card")
