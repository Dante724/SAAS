"""Tekly Go pilot: remember which app made a change.

Adds a nullable `client` column ("go/<version>" | "web" | NULL for no request) to
order_status_logs, order_comments and audit_log. Filled from the X-Tekly-Client request header
(backend/core/client_source.py). Existing rows stay NULL; the pilot report only reads new ones.

Revision ID: gp144g4
Revises: gc144g3
"""
import sqlalchemy as sa
from alembic import op

revision = "gp144g4"
down_revision = "gc144g3"
branch_labels = None
depends_on = None

_TABLES = ("order_status_logs", "order_comments", "audit_log")


def upgrade() -> None:
    for t in _TABLES:
        with op.batch_alter_table(t) as b:
            b.add_column(sa.Column("client", sa.String(32), nullable=True))


def downgrade() -> None:
    for t in reversed(_TABLES):
        with op.batch_alter_table(t) as b:
            b.drop_column("client")
