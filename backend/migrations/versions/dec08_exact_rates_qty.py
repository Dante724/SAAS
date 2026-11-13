"""Audit DB-08 / FIN-14: money-affecting rates, percentages, points and quantities: Float -> NUMERIC.

The money columns were already NUMERIC; the ORM now reads all of them (and these) as exact Decimals
(``backend.db.exact.Exact``). These were still binary floats in the database:

  currency and FX rates        NUMERIC(18,8)
  percentages and tax rates    NUMERIC(9,4)   (also order_lines.work_share)
  loyalty earn rate            NUMERIC(12,4)
  loyalty points               NUMERIC(14,2)
  stock and line quantities    NUMERIC(14,4)

Measures stay Float on purpose (mm, kg, GPS, hours, meter readings, thresholds).

Postgres converts each value with ``round(col::numeric, scale)`` (half away from zero), so stored
binary noise such as 2.9999999999999996 becomes 3.0000. This rewrites the affected tables
(order_lines, stock_movements and stock_levels are the large ones), so run it in a quiet window.
Downgrade casts back to double precision; values that were rounded stay rounded.

Revision ID: dec08
Revises: w2b01
"""
import sqlalchemy as sa
from alembic import op

revision = "dec08"
down_revision = "w2b01"
branch_labels = None
depends_on = None

COLUMNS = {
    "accounts": {"fee_pct": (9, 4)},
    "bundle_components": {"qty": (14, 4)},
    "client_return_lines": {"qty": (14, 4)},
    "currencies": {"rate": (18, 8)},
    "currency_rate_history": {"rate": (18, 8)},
    "employees": {"commission_labour": (9, 4), "commission_parts": (9, 4)},
    "invoice_lines": {"qty": (14, 4)},
    "loyalty_ledger": {"points": (14, 2)},
    "membership_plan_items": {"pct": (9, 4)},
    "membership_usage": {"qty": (14, 4)},
    "order_lines": {"bundle_qty": (14, 4), "pending_stock": (14, 4), "qty": (14, 4), "tax_rate": (9, 4), "work_share": (9, 4)},
    "orders": {"fx_rate": (18, 8)},
    "organisations": {"discount_approval_pct": (9, 4), "loyalty_earn_rate": (12, 4), "membership_prompt_threshold_pct": (9, 4), "min_margin_pct": (9, 4)},
    "parts": {"min_order_qty": (14, 4)},
    "platform_coupons": {"percent": (9, 4)},
    "po_lines": {"qty_ordered": (14, 4), "qty_received": (14, 4)},
    "posting_lines": {"markup_pct": (9, 4), "qty": (14, 4)},
    "price_type_tiers": {"markup_pct": (9, 4), "min_qty": (14, 4)},
    "price_types": {"markup_pct": (9, 4)},
    "purchase_return_lines": {"qty": (14, 4)},
    "recommendations": {"qty": (14, 4)},
    "sale_lines": {"qty": (14, 4), "tax_rate": (9, 4)},
    "stock_levels": {"qty_available": (14, 4), "qty_reserved": (14, 4)},
    "stock_movements": {"qty": (14, 4)},
    "stock_reservations": {"qty": (14, 4)},
    "stock_transfer_lines": {"qty": (14, 4), "qty_received": (14, 4)},
    "stocktake_lines": {"counted_qty": (14, 4), "system_qty": (14, 4), "variance": (14, 4)},
    "taxes": {"rate": (9, 4)},
    "transactions": {"fx_rate": (18, 8)},
    "write_off_lines": {"qty": (14, 4)},
}


def _pg() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    for table, cols in COLUMNS.items():
        if _pg():
            for col, (p, s) in cols.items():
                op.execute(f'ALTER TABLE {table} ALTER COLUMN {col} TYPE NUMERIC({p},{s}) '
                           f'USING round({col}::numeric, {s})')
        else:
            with op.batch_alter_table(table) as b:
                for col, (p, s) in cols.items():
                    b.alter_column(col, type_=sa.Numeric(p, s), existing_type=sa.Float())


def downgrade() -> None:
    for table, cols in COLUMNS.items():
        if _pg():
            for col in cols:
                op.execute(f"ALTER TABLE {table} ALTER COLUMN {col} TYPE DOUBLE PRECISION "
                           f"USING {col}::double precision")
        else:
            with op.batch_alter_table(table) as b:
                for col, (p, s) in cols.items():
                    b.alter_column(col, type_=sa.Float(), existing_type=sa.Numeric(p, s))
