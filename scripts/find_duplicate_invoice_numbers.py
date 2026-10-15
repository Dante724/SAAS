"""Read-only audit: invoice numbers used more than once within an organisation.

Why: until security phase 3, invoices were numbered by three different schemes (an unlocked
sequence plus two "count + 1" paths), which could hand out the same number twice. New invoices
now share one locked sequence. Existing duplicates must be resolved before a
UNIQUE(org_id, invoice_number) constraint can be added — this lists them.

SAFE ON PRODUCTION: only SELECTs; on Postgres the transaction is READ ONLY; always rolled back.

Usage (uses the app's DATABASE_URL):
    DATABASE_URL=... python scripts/find_duplicate_invoice_numbers.py
Exit code: 0 = none, 1 = duplicates found (listed), 2 = could not run.
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.disable(logging.CRITICAL)

from sqlalchemy import func, select, text  # noqa: E402


async def main() -> int:
    from backend.db.database import AsyncSessionLocal
    from backend.models import Invoice, Organisation

    try:
        async with AsyncSessionLocal() as db:
            if db.bind.dialect.name == "postgresql":
                await db.execute(text("SET TRANSACTION READ ONLY"))
            rows = (await db.execute(
                select(Organisation.name, Invoice.invoice_number, func.count(Invoice.id))
                .join(Organisation, Organisation.id == Invoice.org_id)
                .group_by(Organisation.name, Invoice.invoice_number)
                .having(func.count(Invoice.id) > 1)
                .order_by(Organisation.name, Invoice.invoice_number))).all()
            await db.rollback()
    except Exception as e:  # noqa: BLE001
        print(f"Could not query the database: {type(e).__name__}: {e}")
        return 2
    if not rows:
        print("No duplicate invoice numbers.")
        return 0
    print(f"{len(rows)} invoice numbers are used more than once:")
    for org, number, n in rows:
        print(f"  {org}: {number} x{n}")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
