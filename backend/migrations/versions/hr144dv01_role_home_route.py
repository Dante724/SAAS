"""144dv — Role.home_route: per-role landing page (role-redirect).

Nullable string route (e.g. "/my-work"). NULL means the default Home ("/"), so
existing roles are unchanged.

Revision ID: hr144dv01
Revises: hf144ds01
Create Date: 2026-10-12
"""
from alembic import op
import sqlalchemy as sa

revision = "hr144dv01"
down_revision = "hf144ds01"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("roles") as b:
        b.add_column(sa.Column("home_route", sa.String(length=40), nullable=True))


def downgrade():
    with op.batch_alter_table("roles") as b:
        b.drop_column("home_route")
