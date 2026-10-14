"""Build 144ee — richer service catalog fields (Orderry New Service parity).

Adds description, is_labor (Labor/Service type), unit (unit of measurement),
and bookable (show on the public booking page) to services.

Revision ID: w3y5a7c9e1g3
Revises: v2x4z6b8d0f2
"""
from alembic import op
import sqlalchemy as sa

revision = "w3y5a7c9e1g3"
down_revision = "v2x4z6b8d0f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("services") as b:
        b.add_column(sa.Column("description", sa.String(length=2000), server_default="", nullable=False))
        b.add_column(sa.Column("is_labor", sa.Boolean(), server_default=sa.false(), nullable=False))
        b.add_column(sa.Column("unit", sa.String(length=24), server_default="pc", nullable=False))
        b.add_column(sa.Column("bookable", sa.Boolean(), server_default=sa.true(), nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("services") as b:
        b.drop_column("bookable")
        b.drop_column("unit")
        b.drop_column("is_labor")
        b.drop_column("description")
