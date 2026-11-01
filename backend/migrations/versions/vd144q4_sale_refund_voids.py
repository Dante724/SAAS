"""Sale and refund voids ("Delete sales" / "Delete refunds").

- sales.coupon_id: the coupon a sale used, so voiding it gives the use back.
- sale_lines.stock_draws: the FIFO cost layers and other jobs' reservations a line drew from,
  so a void returns stock exactly where it came from.
- refunds.payment_id: the refund Payment row the refund created.
- sales / refunds voided_at, voided_by_id, void_reason: a voided document stays on record.

Revision ID: vd144q4
Revises: pc144q3
"""
import sqlalchemy as sa
from alembic import op

revision = "vd144q4"
down_revision = "pc144q3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("sales") as b:
        b.add_column(sa.Column("coupon_id", sa.String(), nullable=True))
        b.add_column(sa.Column("voided_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("voided_by_id", sa.String(), nullable=True))
        b.add_column(sa.Column("void_reason", sa.String(length=300), nullable=True))
        b.create_foreign_key("fk_sales_voided_by_id_employees", "employees", ["voided_by_id"], ["id"])
    with op.batch_alter_table("sale_lines") as b:
        b.add_column(sa.Column("stock_draws", sa.JSON(), nullable=True))
    with op.batch_alter_table("refunds") as b:
        b.add_column(sa.Column("payment_id", sa.String(), nullable=True))
        b.add_column(sa.Column("voided_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("voided_by_id", sa.String(), nullable=True))
        b.add_column(sa.Column("void_reason", sa.String(length=300), nullable=True))
        b.create_foreign_key("fk_refunds_payment_id_payments", "payments", ["payment_id"], ["id"])
        b.create_foreign_key("fk_refunds_voided_by_id_employees", "employees", ["voided_by_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("refunds") as b:
        b.drop_constraint("fk_refunds_voided_by_id_employees", type_="foreignkey")
        b.drop_constraint("fk_refunds_payment_id_payments", type_="foreignkey")
        for col in ("void_reason", "voided_by_id", "voided_at", "payment_id"):
            b.drop_column(col)
    with op.batch_alter_table("sale_lines") as b:
        b.drop_column("stock_draws")
    with op.batch_alter_table("sales") as b:
        b.drop_constraint("fk_sales_voided_by_id_employees", type_="foreignkey")
        for col in ("void_reason", "voided_by_id", "voided_at", "coupon_id"):
            b.drop_column(col)
