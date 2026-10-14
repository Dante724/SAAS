"""build 144al — editable purchase orders: supplier invoice, charges, notes

Revision ID: d2f4a6b8c0e1
Revises: c1e3f5a7b9d2
Create Date: 2026-08-07

Adds supplier-document fields and optional customizable charges to purchase orders,
plus an org-level list of charge types.
"""
import sqlalchemy as sa
from alembic import op

revision = "d2f4a6b8c0e1"
down_revision = "c1e3f5a7b9d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("purchase_orders") as batch:
        batch.add_column(sa.Column("supplier_invoice_no", sa.String(length=80), nullable=True))
        batch.add_column(sa.Column("supplier_invoice_date", sa.Date(), nullable=True))
        batch.add_column(sa.Column("notes", sa.Text(), nullable=True))
        batch.add_column(sa.Column("charges", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("charges_to_cost", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))
    with op.batch_alter_table("organisations") as batch:
        batch.add_column(sa.Column("po_charge_types", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("organisations") as batch:
        batch.drop_column("po_charge_types")
    with op.batch_alter_table("purchase_orders") as batch:
        for col in ("charges_to_cost", "charges", "notes", "supplier_invoice_date", "supplier_invoice_no"):
            batch.drop_column(col)
