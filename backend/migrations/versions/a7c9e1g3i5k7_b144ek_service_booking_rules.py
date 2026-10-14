"""Build 144ek — service online-booking rules.

Adds allowed_employee_ids (who can perform) and required_resource_types to
services, plus (via the price-types system, no schema change) prices-by-type.

Revision ID: a7c9e1g3i5k7
Revises: z6b8d0f2h4j6
"""
from alembic import op
import sqlalchemy as sa

revision = "a7c9e1g3i5k7"
down_revision = "z6b8d0f2h4j6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("services") as b:
        b.add_column(sa.Column("allowed_employee_ids", sa.JSON(), nullable=True))
        b.add_column(sa.Column("required_resource_types", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("services") as b:
        b.drop_column("required_resource_types")
        b.drop_column("allowed_employee_ids")
