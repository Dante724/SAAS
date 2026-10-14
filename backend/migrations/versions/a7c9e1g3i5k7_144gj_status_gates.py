"""No-op — this filename once collided with an existing revision id. The real
status-gates migration is g5i7k9m1o3q5. This file can't be deleted (mounted FS),
so it's a valid empty revision chained at the head to keep Alembic's scan happy.

Revision ID: n2p4r6t8v0x2
Revises: m1o3q5s7u9w1
"""
from alembic import op  # noqa: F401
import sqlalchemy as sa  # noqa: F401

revision = "n2p4r6t8v0x2"
down_revision = "m1o3q5s7u9w1"
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
