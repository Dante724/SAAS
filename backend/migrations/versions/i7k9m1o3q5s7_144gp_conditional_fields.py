"""144gp — Smart Fields Phase A: conditional visibility on custom fields.

Revision ID: i7k9m1o3q5s7
Revises: h6j8l0n2p4r6
"""
from alembic import op
import sqlalchemy as sa

revision = "i7k9m1o3q5s7"
down_revision = "h6j8l0n2p4r6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("custom_fields", sa.Column("show_when_field_key", sa.String(length=100), nullable=True))
    op.add_column("custom_fields", sa.Column("show_when_op", sa.String(length=10), nullable=True))
    op.add_column("custom_fields", sa.Column("show_when_value", sa.String(length=200), nullable=True))


def downgrade():
    op.drop_column("custom_fields", "show_when_value")
    op.drop_column("custom_fields", "show_when_op")
    op.drop_column("custom_fields", "show_when_field_key")
