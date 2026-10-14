"""Build 144bc — cash drawer: denomination columns on cash_shifts + cash_drawer_movements table.

Revision ID: dr144bc01
Revises: fk144as01
"""
import sqlalchemy as sa
from alembic import op

revision = "dr144bc01"
down_revision = "fk144as01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("cash_shifts") as b:
        b.add_column(sa.Column("open_denoms", sa.JSON(), nullable=True))
        b.add_column(sa.Column("close_denoms", sa.JSON(), nullable=True))
    op.create_table(
        "cash_drawer_movements",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id"), nullable=False),
        sa.Column("shift_id", sa.String(), sa.ForeignKey("cash_shifts.id"), nullable=False),
        sa.Column("direction", sa.String(length=4), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("note", sa.String(length=300), nullable=False),
        sa.Column("created_by_id", sa.String(), sa.ForeignKey("employees.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_cash_drawer_movements_org_id", "cash_drawer_movements", ["org_id"])
    op.create_index("ix_cash_drawer_movements_shift_id", "cash_drawer_movements", ["shift_id"])


def downgrade() -> None:
    op.drop_table("cash_drawer_movements")
    with op.batch_alter_table("cash_shifts") as b:
        b.drop_column("close_denoms")
        b.drop_column("open_denoms")
