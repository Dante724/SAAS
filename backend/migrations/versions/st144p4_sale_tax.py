"""POS sales carry tax.

Adds sales.tax_total and sale_lines.tax_amount / tax_detail (JSON breakdown per tax). Existing
sales were never taxed, so 0 / NULL is their true value — no backfill.

Revision ID: st144p4
Revises: mr144p4
"""
import sqlalchemy as sa
from alembic import op

revision = "st144p4"
down_revision = "mr144p4"
branch_labels = None
depends_on = None


def _cols(table: str) -> set:
    insp = sa.inspect(op.get_bind())
    return {c["name"] for c in insp.get_columns(table)} if insp.has_table(table) else set()


def upgrade() -> None:
    if "tax_total" not in _cols("sales"):
        with op.batch_alter_table("sales") as b:
            b.add_column(sa.Column("tax_total", sa.Numeric(12, 2, asdecimal=False), nullable=False,
                                   server_default="0"))
    cols = _cols("sale_lines")
    with op.batch_alter_table("sale_lines") as b:
        if "tax_amount" not in cols:
            b.add_column(sa.Column("tax_amount", sa.Numeric(12, 2, asdecimal=False), nullable=False,
                                   server_default="0"))
        if "tax_detail" not in cols:
            b.add_column(sa.Column("tax_detail", sa.Text(), nullable=True))


def downgrade() -> None:
    cols = _cols("sale_lines")
    with op.batch_alter_table("sale_lines") as b:
        if "tax_detail" in cols:
            b.drop_column("tax_detail")
        if "tax_amount" in cols:
            b.drop_column("tax_amount")
    if "tax_total" in _cols("sales"):
        with op.batch_alter_table("sales") as b:
            b.drop_column("tax_total")
