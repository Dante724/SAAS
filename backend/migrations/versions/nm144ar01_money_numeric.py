"""Build 144ar — money columns Float -> Numeric(12,2) for exact storage.

Stores money as exact NUMERIC in Postgres while the ORM maps the columns with
``asdecimal=False`` so Python continues to receive floats (no arithmetic changes).
``parts.avg_cost`` uses NUMERIC(12,4) as a cost-basis column. All other Float
columns (quantities, rates, percentages, hours, GPS, thresholds, etc.) are left
as Float. Reversible: downgrade() restores Float.

Revision ID: nm144ar01
Revises: ix144an01
"""
from alembic import op
import sqlalchemy as sa

revision = "nm144ar01"
down_revision = "ix144an01"
branch_labels = None
depends_on = None

NUM2 = sa.Numeric(12, 2, asdecimal=False)
NUM4 = sa.Numeric(12, 4, asdecimal=False)


def upgrade() -> None:

    with op.batch_alter_table("accounts") as b:
        b.alter_column("balance", type_=NUM2, existing_type=sa.Float())
        b.alter_column("fee_fixed", type_=NUM2, existing_type=sa.Float())

    with op.batch_alter_table("bundles") as b:
        b.alter_column("price", type_=NUM2, existing_type=sa.Float())

    with op.batch_alter_table("cash_shifts") as b:
        b.alter_column("closing_float", type_=NUM2, existing_type=sa.Float())
        b.alter_column("expected_cash", type_=NUM2, existing_type=sa.Float())
        b.alter_column("opening_float", type_=NUM2, existing_type=sa.Float())

    with op.batch_alter_table("clients") as b:
        b.alter_column("balance", type_=NUM2, existing_type=sa.Float())

    with op.batch_alter_table("coupons") as b:
        b.alter_column("min_total", type_=NUM2, existing_type=sa.Float())

    with op.batch_alter_table("devices") as b:
        b.alter_column("purchase_price", type_=NUM2, existing_type=sa.Float())
        b.alter_column("sale_price", type_=NUM2, existing_type=sa.Float())

    with op.batch_alter_table("document_signatures") as b:
        b.alter_column("doc_total", type_=NUM2, existing_type=sa.Float())

    with op.batch_alter_table("expenses") as b:
        b.alter_column("amount", type_=NUM2, existing_type=sa.Float())

    with op.batch_alter_table("gift_cards") as b:
        b.alter_column("balance", type_=NUM2, existing_type=sa.Float())
        b.alter_column("initial_value", type_=NUM2, existing_type=sa.Float())

    with op.batch_alter_table("gift_card_ledger") as b:
        b.alter_column("amount", type_=NUM2, existing_type=sa.Float())

    with op.batch_alter_table("invoices") as b:
        b.alter_column("amount_paid", type_=NUM2, existing_type=sa.Float())
        b.alter_column("discount", type_=NUM2, existing_type=sa.Float())
        b.alter_column("subtotal", type_=NUM2, existing_type=sa.Float())
        b.alter_column("tax", type_=NUM2, existing_type=sa.Float())
        b.alter_column("total", type_=NUM2, existing_type=sa.Float())

    with op.batch_alter_table("invoice_lines") as b:
        b.alter_column("total", type_=NUM2, existing_type=sa.Float())
        b.alter_column("unit_price", type_=NUM2, existing_type=sa.Float())

    with op.batch_alter_table("orders") as b:
        b.alter_column("deposit_amount", type_=NUM2, existing_type=sa.Float())
        b.alter_column("discount", type_=NUM2, existing_type=sa.Float())
        b.alter_column("labour_total", type_=NUM2, existing_type=sa.Float())
        b.alter_column("parts_total", type_=NUM2, existing_type=sa.Float())
        b.alter_column("prepayment_total", type_=NUM2, existing_type=sa.Float())
        b.alter_column("tax", type_=NUM2, existing_type=sa.Float())
        b.alter_column("total", type_=NUM2, existing_type=sa.Float())

    with op.batch_alter_table("order_lines") as b:
        b.alter_column("cost_price", type_=NUM2, existing_type=sa.Float())
        b.alter_column("discount", type_=NUM2, existing_type=sa.Float())
        b.alter_column("total", type_=NUM2, existing_type=sa.Float())
        b.alter_column("unit_price", type_=NUM2, existing_type=sa.Float())

    with op.batch_alter_table("parts") as b:
        b.alter_column("cost_price", type_=NUM2, existing_type=sa.Float())
        b.alter_column("repair_price", type_=NUM2, existing_type=sa.Float())
        b.alter_column("sell_price", type_=NUM2, existing_type=sa.Float())

    with op.batch_alter_table("payments") as b:
        b.alter_column("amount", type_=NUM2, existing_type=sa.Float())

    with op.batch_alter_table("payment_intents") as b:
        b.alter_column("amount", type_=NUM2, existing_type=sa.Float())

    with op.batch_alter_table("price_type_items") as b:
        b.alter_column("price", type_=NUM2, existing_type=sa.Float())

    with op.batch_alter_table("purchase_orders") as b:
        b.alter_column("freight_capitalised", type_=NUM2, existing_type=sa.Float())
        b.alter_column("received_cost", type_=NUM2, existing_type=sa.Float())
        b.alter_column("shipment_freight", type_=NUM2, existing_type=sa.Float())
        b.alter_column("supplier_invoice_total", type_=NUM2, existing_type=sa.Float())
        b.alter_column("total", type_=NUM2, existing_type=sa.Float())

    with op.batch_alter_table("po_lines") as b:
        b.alter_column("unit_cost", type_=NUM2, existing_type=sa.Float())

    with op.batch_alter_table("recommendations") as b:
        b.alter_column("unit_price", type_=NUM2, existing_type=sa.Float())

    with op.batch_alter_table("refunds") as b:
        b.alter_column("amount", type_=NUM2, existing_type=sa.Float())

    with op.batch_alter_table("sales") as b:
        b.alter_column("discount", type_=NUM2, existing_type=sa.Float())
        b.alter_column("total", type_=NUM2, existing_type=sa.Float())
        b.alter_column("total_cost", type_=NUM2, existing_type=sa.Float())

    with op.batch_alter_table("sale_lines") as b:
        b.alter_column("cost_price", type_=NUM2, existing_type=sa.Float())
        b.alter_column("discount", type_=NUM2, existing_type=sa.Float())
        b.alter_column("total", type_=NUM2, existing_type=sa.Float())
        b.alter_column("unit_price", type_=NUM2, existing_type=sa.Float())

    with op.batch_alter_table("services") as b:
        b.alter_column("cost_price", type_=NUM2, existing_type=sa.Float())
        b.alter_column("price", type_=NUM2, existing_type=sa.Float())

    with op.batch_alter_table("stock_movements") as b:
        b.alter_column("unit_cost", type_=NUM2, existing_type=sa.Float())

    with op.batch_alter_table("suppliers") as b:
        b.alter_column("balance", type_=NUM2, existing_type=sa.Float())

    with op.batch_alter_table("transactions") as b:
        b.alter_column("amount", type_=NUM2, existing_type=sa.Float())
        b.alter_column("balance_after", type_=NUM2, existing_type=sa.Float())

    with op.batch_alter_table("wage_records") as b:
        b.alter_column("base_amount", type_=NUM2, existing_type=sa.Float())
        b.alter_column("bonus", type_=NUM2, existing_type=sa.Float())
        b.alter_column("commission_amount", type_=NUM2, existing_type=sa.Float())
        b.alter_column("deductions", type_=NUM2, existing_type=sa.Float())
        b.alter_column("net_payable", type_=NUM2, existing_type=sa.Float())

    with op.batch_alter_table("checklist_template_items") as b:
        b.alter_column("upsell_price", type_=NUM2, existing_type=sa.Float())

    with op.batch_alter_table("custom_fields") as b:
        b.alter_column("upsell_price", type_=NUM2, existing_type=sa.Float())

    with op.batch_alter_table("order_checklist_items") as b:
        b.alter_column("upsell_price", type_=NUM2, existing_type=sa.Float())

    with op.batch_alter_table("parts") as b:
        b.alter_column("avg_cost", type_=NUM4, existing_type=sa.Float())


def downgrade() -> None:
    with op.batch_alter_table("accounts") as b:
        b.alter_column("balance", type_=sa.Float(), existing_type=NUM2)
        b.alter_column("fee_fixed", type_=sa.Float(), existing_type=NUM2)

    with op.batch_alter_table("bundles") as b:
        b.alter_column("price", type_=sa.Float(), existing_type=NUM2)

    with op.batch_alter_table("cash_shifts") as b:
        b.alter_column("closing_float", type_=sa.Float(), existing_type=NUM2)
        b.alter_column("expected_cash", type_=sa.Float(), existing_type=NUM2)
        b.alter_column("opening_float", type_=sa.Float(), existing_type=NUM2)

    with op.batch_alter_table("clients") as b:
        b.alter_column("balance", type_=sa.Float(), existing_type=NUM2)

    with op.batch_alter_table("coupons") as b:
        b.alter_column("min_total", type_=sa.Float(), existing_type=NUM2)

    with op.batch_alter_table("devices") as b:
        b.alter_column("purchase_price", type_=sa.Float(), existing_type=NUM2)
        b.alter_column("sale_price", type_=sa.Float(), existing_type=NUM2)

    with op.batch_alter_table("document_signatures") as b:
        b.alter_column("doc_total", type_=sa.Float(), existing_type=NUM2)

    with op.batch_alter_table("expenses") as b:
        b.alter_column("amount", type_=sa.Float(), existing_type=NUM2)

    with op.batch_alter_table("gift_cards") as b:
        b.alter_column("balance", type_=sa.Float(), existing_type=NUM2)
        b.alter_column("initial_value", type_=sa.Float(), existing_type=NUM2)

    with op.batch_alter_table("gift_card_ledger") as b:
        b.alter_column("amount", type_=sa.Float(), existing_type=NUM2)

    with op.batch_alter_table("invoices") as b:
        b.alter_column("amount_paid", type_=sa.Float(), existing_type=NUM2)
        b.alter_column("discount", type_=sa.Float(), existing_type=NUM2)
        b.alter_column("subtotal", type_=sa.Float(), existing_type=NUM2)
        b.alter_column("tax", type_=sa.Float(), existing_type=NUM2)
        b.alter_column("total", type_=sa.Float(), existing_type=NUM2)

    with op.batch_alter_table("invoice_lines") as b:
        b.alter_column("total", type_=sa.Float(), existing_type=NUM2)
        b.alter_column("unit_price", type_=sa.Float(), existing_type=NUM2)

    with op.batch_alter_table("orders") as b:
        b.alter_column("deposit_amount", type_=sa.Float(), existing_type=NUM2)
        b.alter_column("discount", type_=sa.Float(), existing_type=NUM2)
        b.alter_column("labour_total", type_=sa.Float(), existing_type=NUM2)
        b.alter_column("parts_total", type_=sa.Float(), existing_type=NUM2)
        b.alter_column("prepayment_total", type_=sa.Float(), existing_type=NUM2)
        b.alter_column("tax", type_=sa.Float(), existing_type=NUM2)
        b.alter_column("total", type_=sa.Float(), existing_type=NUM2)

    with op.batch_alter_table("order_lines") as b:
        b.alter_column("cost_price", type_=sa.Float(), existing_type=NUM2)
        b.alter_column("discount", type_=sa.Float(), existing_type=NUM2)
        b.alter_column("total", type_=sa.Float(), existing_type=NUM2)
        b.alter_column("unit_price", type_=sa.Float(), existing_type=NUM2)

    with op.batch_alter_table("parts") as b:
        b.alter_column("cost_price", type_=sa.Float(), existing_type=NUM2)
        b.alter_column("repair_price", type_=sa.Float(), existing_type=NUM2)
        b.alter_column("sell_price", type_=sa.Float(), existing_type=NUM2)

    with op.batch_alter_table("payments") as b:
        b.alter_column("amount", type_=sa.Float(), existing_type=NUM2)

    with op.batch_alter_table("payment_intents") as b:
        b.alter_column("amount", type_=sa.Float(), existing_type=NUM2)

    with op.batch_alter_table("price_type_items") as b:
        b.alter_column("price", type_=sa.Float(), existing_type=NUM2)

    with op.batch_alter_table("purchase_orders") as b:
        b.alter_column("freight_capitalised", type_=sa.Float(), existing_type=NUM2)
        b.alter_column("received_cost", type_=sa.Float(), existing_type=NUM2)
        b.alter_column("shipment_freight", type_=sa.Float(), existing_type=NUM2)
        b.alter_column("supplier_invoice_total", type_=sa.Float(), existing_type=NUM2)
        b.alter_column("total", type_=sa.Float(), existing_type=NUM2)

    with op.batch_alter_table("po_lines") as b:
        b.alter_column("unit_cost", type_=sa.Float(), existing_type=NUM2)

    with op.batch_alter_table("recommendations") as b:
        b.alter_column("unit_price", type_=sa.Float(), existing_type=NUM2)

    with op.batch_alter_table("refunds") as b:
        b.alter_column("amount", type_=sa.Float(), existing_type=NUM2)

    with op.batch_alter_table("sales") as b:
        b.alter_column("discount", type_=sa.Float(), existing_type=NUM2)
        b.alter_column("total", type_=sa.Float(), existing_type=NUM2)
        b.alter_column("total_cost", type_=sa.Float(), existing_type=NUM2)

    with op.batch_alter_table("sale_lines") as b:
        b.alter_column("cost_price", type_=sa.Float(), existing_type=NUM2)
        b.alter_column("discount", type_=sa.Float(), existing_type=NUM2)
        b.alter_column("total", type_=sa.Float(), existing_type=NUM2)
        b.alter_column("unit_price", type_=sa.Float(), existing_type=NUM2)

    with op.batch_alter_table("services") as b:
        b.alter_column("cost_price", type_=sa.Float(), existing_type=NUM2)
        b.alter_column("price", type_=sa.Float(), existing_type=NUM2)

    with op.batch_alter_table("stock_movements") as b:
        b.alter_column("unit_cost", type_=sa.Float(), existing_type=NUM2)

    with op.batch_alter_table("suppliers") as b:
        b.alter_column("balance", type_=sa.Float(), existing_type=NUM2)

    with op.batch_alter_table("transactions") as b:
        b.alter_column("amount", type_=sa.Float(), existing_type=NUM2)
        b.alter_column("balance_after", type_=sa.Float(), existing_type=NUM2)

    with op.batch_alter_table("wage_records") as b:
        b.alter_column("base_amount", type_=sa.Float(), existing_type=NUM2)
        b.alter_column("bonus", type_=sa.Float(), existing_type=NUM2)
        b.alter_column("commission_amount", type_=sa.Float(), existing_type=NUM2)
        b.alter_column("deductions", type_=sa.Float(), existing_type=NUM2)
        b.alter_column("net_payable", type_=sa.Float(), existing_type=NUM2)

    with op.batch_alter_table("checklist_template_items") as b:
        b.alter_column("upsell_price", type_=sa.Float(), existing_type=NUM2)

    with op.batch_alter_table("custom_fields") as b:
        b.alter_column("upsell_price", type_=sa.Float(), existing_type=NUM2)

    with op.batch_alter_table("order_checklist_items") as b:
        b.alter_column("upsell_price", type_=sa.Float(), existing_type=NUM2)

    with op.batch_alter_table("parts") as b:
        b.alter_column("avg_cost", type_=sa.Float(), existing_type=NUM4)
