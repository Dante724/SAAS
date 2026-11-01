"""Till returns know their sale and what came back (QA final gate C-6, 1 Nov 2026).

client_returns.sale_id, amount (what the items were worth to the customer, tax included: the cash
refund plus any gift-card part), tax (the tax inside it) and cost (the items' cost, back on the
shelf). Net sales takes a till return off revenue, tax and cost of goods with these instead of
the cash refund, which was gross and missed the gift-card part.

Revision ID: ret01
Revises: syn01
"""
import sqlalchemy as sa
from alembic import op

revision = "ret01"
down_revision = "syn01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("client_returns") as b:
        b.add_column(sa.Column("sale_id", sa.String(), sa.ForeignKey("sales.id", name="fk_client_returns_sale_id_sales"),
                               nullable=True))
        b.add_column(sa.Column("amount", sa.Numeric(12, 2, asdecimal=False), nullable=True))
        b.add_column(sa.Column("tax", sa.Numeric(12, 2, asdecimal=False), nullable=True))
        b.add_column(sa.Column("cost", sa.Numeric(12, 2, asdecimal=False), nullable=True))
    op.create_index("ix_client_returns_sale_id", "client_returns", ["sale_id"])


def downgrade() -> None:
    op.drop_index("ix_client_returns_sale_id", table_name="client_returns")
    with op.batch_alter_table("client_returns") as b:
        b.drop_column("cost")
        b.drop_column("tax")
        b.drop_column("amount")
        b.drop_column("sale_id")
