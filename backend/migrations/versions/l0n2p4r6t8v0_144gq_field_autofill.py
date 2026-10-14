"""144gq — auto-fill source on custom fields.

Revision ID: l0n2p4r6t8v0
Revises: k9m1o3q5s7u9
"""
from alembic import op
import sqlalchemy as sa

revision = "l0n2p4r6t8v0"
down_revision = "k9m1o3q5s7u9"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("custom_fields", sa.Column("autofill_source", sa.String(length=80), nullable=True))


def downgrade():
    op.drop_column("custom_fields", "autofill_source")
