"""b144cg — signup onboarding fields (region, employee_count)

Revision ID: n4p6r8t0v2x4
Revises: m3o5q7s9u1w3
Create Date: 2026-08-20
"""
from alembic import op
import sqlalchemy as sa

revision = "n4p6r8t0v2x4"
down_revision = "m3o5q7s9u1w3"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("organisations") as b:
        b.add_column(sa.Column("region", sa.String(length=20), nullable=True))
        b.add_column(sa.Column("employee_count", sa.String(length=20), nullable=True))


def downgrade():
    with op.batch_alter_table("organisations") as b:
        b.drop_column("employee_count")
        b.drop_column("region")
