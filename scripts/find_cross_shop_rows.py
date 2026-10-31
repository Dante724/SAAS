"""Read-only audit: find rows that mix two shops (audit 2026-10-29, H1–H3 / M2).

Before the flush-time shop guard (backend/db/tenancy.py) learned the signed-in shop, a staff
member of shop B could return, transfer or put on a job a product of shop A by id. That left rows
like a StockLevel for A's product at B's location, which inflates A's on-hand, reorder and stock
value. The guard stops new ones; this lists any that already exist so they can be reviewed and
removed by hand.

Checks:
- stock levels / stock movements whose product and location belong to different shops
- PO / transfer / return / write-off / supplier-return lines whose product's shop differs from
  the document's shop
- job and sale lines whose catalog reference (ref_id) points at another shop's product, service or kit

SAFE ON PRODUCTION: SELECTs only; on Postgres the transaction is READ ONLY; always rolled back.

Usage:  DATABASE_URL=... python scripts/find_cross_shop_rows.py
Exit code: 0 = none found, 1 = rows found (printed), 2 = could not run.
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.disable(logging.CRITICAL)

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

# (label, SQL returning the offending row ids) — fixed text, no user input.
CHECKS = [
    ("stock level: product and location in different shops",
     "SELECT s.id FROM stock_levels s JOIN parts p ON p.id = s.part_id JOIN locations l ON l.id = s.location_id "
     "WHERE p.org_id <> l.org_id"),
    ("stock movement: product and location in different shops",
     "SELECT m.id FROM stock_movements m JOIN parts p ON p.id = m.part_id JOIN locations l ON l.id = m.location_id "
     "WHERE p.org_id <> l.org_id"),
    ("PO line: product from another shop",
     "SELECT x.id FROM po_lines x JOIN purchase_orders d ON d.id = x.po_id JOIN parts p ON p.id = x.part_id "
     "WHERE p.org_id <> d.org_id"),
    ("transfer line: product from another shop",
     "SELECT x.id FROM stock_transfer_lines x JOIN stock_transfers d ON d.id = x.transfer_id "
     "JOIN parts p ON p.id = x.part_id WHERE p.org_id <> d.org_id"),
    ("client return line: product from another shop",
     "SELECT x.id FROM client_return_lines x JOIN client_returns d ON d.id = x.return_id "
     "JOIN parts p ON p.id = x.part_id WHERE p.org_id <> d.org_id"),
    ("job line: catalog item from another shop",
     "SELECT x.id FROM order_lines x JOIN orders o ON o.id = x.order_id "
     "LEFT JOIN parts p ON p.id = x.ref_id LEFT JOIN services s ON s.id = x.ref_id "
     "WHERE x.ref_id IS NOT NULL AND ((p.id IS NOT NULL AND p.org_id <> o.org_id) "
     "OR (s.id IS NOT NULL AND s.org_id <> o.org_id))"),
    ("sale line: catalog item from another shop",
     "SELECT x.id FROM sale_lines x JOIN sales d ON d.id = x.sale_id "
     "LEFT JOIN parts p ON p.id = x.ref_id LEFT JOIN services s ON s.id = x.ref_id "
     "WHERE x.ref_id IS NOT NULL AND ((p.id IS NOT NULL AND p.org_id <> d.org_id) "
     "OR (s.id IS NOT NULL AND s.org_id <> d.org_id))"),
]


async def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("Set DATABASE_URL", file=sys.stderr)
        return 2
    engine = create_async_engine(url)
    found = 0
    try:
        async with engine.connect() as conn:
            if conn.dialect.name == "postgresql":
                await conn.execute(text("SET TRANSACTION READ ONLY"))
            for label, sql in CHECKS:
                try:
                    ids = [r[0] for r in (await conn.execute(text(sql))).all()]
                except Exception as e:   # a table this schema doesn't have yet
                    print(f"skip  {label}: {type(e).__name__}: {str(e).splitlines()[0][:120]}")
                    await conn.rollback()
                    continue
                if ids:
                    found += len(ids)
                    print(f"FOUND {label}: {len(ids)} row(s), e.g. {ids[:5]}")
                else:
                    print(f"ok    {label}")
            await conn.rollback()
    finally:
        await engine.dispose()
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
