"""144jb — notification_rules.record_type + scope (Jobs/Estimates + internal/external)

Revision ID: e9g1i3k5m7o9
Revises: d8f0h2j4l6n8
Create Date: 2026-09-20
"""
from alembic import op
import sqlalchemy as sa

revision = "e9g1i3k5m7o9"
down_revision = "d8f0h2j4l6n8"
branch_labels = None
depends_on = None


def _has_col(bind, table, col):
    insp = sa.inspect(bind)
    try:
        return col in [c["name"] for c in insp.get_columns(table)]
    except Exception:
        return False


def upgrade():
    bind = op.get_bind()
    if not _has_col(bind, "notification_rules", "record_type"):
        op.add_column("notification_rules", sa.Column("record_type", sa.String(length=20), nullable=True))
    if not _has_col(bind, "notification_rules", "scope"):
        op.add_column("notification_rules", sa.Column("scope", sa.String(length=10), nullable=False, server_default="client"))


def downgrade():
    bind = op.get_bind()
    if _has_col(bind, "notification_rules", "scope"):
        op.drop_column("notification_rules", "scope")
    if _has_col(bind, "notification_rules", "record_type"):
        op.drop_column("notification_rules", "record_type")
