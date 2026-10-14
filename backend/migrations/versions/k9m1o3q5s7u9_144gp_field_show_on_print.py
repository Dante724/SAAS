"""144gp Phase C — show_on_print flag on custom fields.

Revision ID: k9m1o3q5s7u9
Revises: j8l0n2p4r6t8
"""
from alembic import op
import sqlalchemy as sa

revision = "k9m1o3q5s7u9"
down_revision = "j8l0n2p4r6t8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("custom_fields", sa.Column("show_on_print", sa.Boolean(), nullable=False, server_default="0"))


def downgrade():
    op.drop_column("custom_fields", "show_on_print")
