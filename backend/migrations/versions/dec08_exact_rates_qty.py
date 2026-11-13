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
binary noise such as 2.9999999999999996 becomes 3.0000. Each table is rewritten ONCE (one
ALTER TABLE with all its columns), but the whole revision runs in one transaction, so the 30 tables
stay locked (ACCESS EXCLUSIVE) until it commits; organisations, orders, order_lines, transactions,
stock_movements and stock_levels are the ones every request touches. Run it in a maintenance window
(DEPLOY.md, "Migrations that lock tables").

Pre-flight, before anything changes: a value NUMERIC(p,s) can't hold (|x| that rounds to 10^(p-s)
or more, +-Infinity, NaN) would abort the ALTER half-way with no list of rows. Instead the upgrade
stops first with a count per column (no row data); fix those values, then run it again.

SQLite (development only): each table is rebuilt in batch mode, and every index that rebuild loses
(expression and partial unique indexes such as uq_parts_org_sku_active) is put back from its saved
SQL, the same way as w2b01.

Downgrade casts back to double precision; values that were rounded stay rounded.

Revision ID: dec08
Revises: w2b01
"""
import importlib.util
from pathlib import Path

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


def _sqlite_rebuild():
    """w2b01's helper: batch-rebuild a SQLite table, then put back every index or trigger the rebuild
    lost (alembic doesn't reflect expression indexes, and partial ones only partly)."""
    path = Path(__file__).with_name("w2b01_one_open_unique_numbers_checks.py")
    spec = importlib.util.spec_from_file_location("_w2b01_helpers", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._sqlite_rebuild


def pg_alter_sql(table: str, cols: dict) -> str:
    """One ALTER TABLE for every column of a table, so Postgres rewrites the table once."""
    return f"ALTER TABLE {table} " + ", ".join(
        f"ALTER COLUMN {col} TYPE NUMERIC({p},{s}) USING round({col}::numeric, {s})"
        for col, (p, s) in cols.items())


def _problem_counts(bind, table: str, col: str, p: int, s: int) -> dict:
    """How many values of table.col NUMERIC(p,s) can't take: {'out of range': n, 'infinite': n, 'NaN': n}."""
    limit = 10 ** (p - s)
    if bind.dialect.name == "postgresql":
        v = f"{col}::float8"
        finite = f"{v} NOT IN ('NaN'::float8, 'Infinity'::float8, '-Infinity'::float8)"
        sql = (f"SELECT count(*) FILTER (WHERE CASE WHEN {finite} "
               f"THEN abs(round({col}::numeric, {s})) >= {limit} ELSE false END), "
               f"count(*) FILTER (WHERE {v} IN ('Infinity'::float8, '-Infinity'::float8)), "
               f"count(*) FILTER (WHERE {v} = 'NaN'::float8) FROM {table}")
    else:   # SQLite stores +-Infinity as REAL and NaN as NULL
        sql = (f"SELECT coalesce(sum(abs({col}) <= 1e308 AND abs(round({col}, {s})) >= {limit}), 0), "
               f"coalesce(sum(abs({col}) > 1e308), 0), 0 FROM {table}")
    out, inf, nan = bind.exec_driver_sql(sql).fetchone()
    return {what: n for what, n in (("out of range", out), ("infinite", inf), ("NaN", nan)) if n}


def _preflight(bind) -> None:
    """Stop before any ALTER if a value can't become NUMERIC: counts per column, never row data."""
    bad = []
    for table, cols in COLUMNS.items():
        for col, (p, s) in cols.items():
            counts = _problem_counts(bind, table, col, p, s)
            if counts:
                bad.append(f"{table}.{col} NUMERIC({p},{s}): "
                           + ", ".join(f"{n} {what}" for what, n in counts.items()))
    if bad:
        raise RuntimeError(
            "dec08 aborted, nothing changed: these values don't fit their new exact type (out of range = "
            "rounds to 10^(p-s) or more; NUMERIC can't hold +-Infinity, and a NaN would break every "
            "comparison the app makes on it):\n  " + "\n  ".join(bad)
            + "\nCorrect them (UPDATE <table> SET <column> = <sane value> WHERE ...), then upgrade again.")


def upgrade() -> None:
    bind = op.get_bind()
    _preflight(bind)
    rebuild = None if _pg() else _sqlite_rebuild()
    for table, cols in COLUMNS.items():
        if rebuild is None:
            op.execute(pg_alter_sql(table, cols))
        else:
            def change(b, cols=cols):
                for col, (p, s) in cols.items():
                    b.alter_column(col, type_=sa.Numeric(p, s), existing_type=sa.Float())
            rebuild(bind, table, change)


def downgrade() -> None:
    bind = op.get_bind()
    rebuild = None if _pg() else _sqlite_rebuild()
    for table, cols in COLUMNS.items():
        if rebuild is None:
            op.execute(f"ALTER TABLE {table} " + ", ".join(
                f"ALTER COLUMN {col} TYPE DOUBLE PRECISION USING {col}::double precision" for col in cols))
        else:
            def change(b, cols=cols):
                for col, (p, s) in cols.items():
                    b.alter_column(col, type_=sa.Float(), existing_type=sa.Numeric(p, s))
            rebuild(bind, table, change)
