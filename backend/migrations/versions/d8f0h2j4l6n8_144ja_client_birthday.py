"""144ja — clients.birthday (Contacts-module birthday notifications)

Revision ID: d8f0h2j4l6n8
Revises: c7e9g1i3k5m7
Create Date: 2026-09-19
"""
from alembic import op
import sqlalchemy as sa

revision = "d8f0h2j4l6n8"
down_revision = "c7e9g1i3k5m7"
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
    if not _has_col(bind, "clients", "birthday"):
        op.add_column("clients", sa.Column("birthday", sa.String(length=10), nullable=True))


def downgrade():
    bind = op.get_bind()
    if _has_col(bind, "clients", "birthday"):
        op.drop_column("clients", "birthday")
