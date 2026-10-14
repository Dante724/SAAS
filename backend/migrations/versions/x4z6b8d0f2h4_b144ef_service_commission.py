"""Build 144ef — service-level exceptional commissions.

Adds service_id to commission_rules so a commission rule can be scoped to a
single service (Orderry "Employee exceptional commissions" on the service form).

Revision ID: x4z6b8d0f2h4
Revises: w3y5a7c9e1g3
"""
from alembic import op
import sqlalchemy as sa

revision = "x4z6b8d0f2h4"
down_revision = "w3y5a7c9e1g3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("commission_rules") as b:
        b.add_column(sa.Column("service_id", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("commission_rules") as b:
        b.drop_column("service_id")
