"""One-off maintenance: purge throwaway inventory test data for a clean slate.

Deletes ALL purchase orders + PO lines, and ALL products (parts) together with their
stock levels, movements, FIFO batches and reservations, then zeroes supplier balances.
Contacts (clients/suppliers) are kept — only inventory rows are cleared.

Safety:
  * Makes a timestamped .bak of the SQLite file first (SQLite only).
  * Runs inside one transaction — either everything goes or nothing does.
  * Prints before/after row counts.

Usage (from the repo root, with the venv active):
    python -m backend.scripts.purge_inventory_test_data          # dry run: shows what WOULD go
    python -m backend.scripts.purge_inventory_test_data --yes    # actually purge

Stop the dev server first so it isn't holding the DB open.
"""
import asyncio
import shutil
import sys
from datetime import datetime

from sqlalchemy import text

from backend.db.database import engine
from backend.config import settings

# child rows first, parents last — respects FK order
_TABLES = [
    "stock_reservations",
    "stock_item_batches",
    "stock_movements",
    "stock_levels",
    "po_lines",
    "purchase_orders",
    "parts",
]


def _backup_sqlite():
    url = settings.DATABASE_URL
    if "sqlite" not in url:
        return None
    # sqlite+aiosqlite:////abs/path.db  ->  /abs/path.db
    path = url.split("///")[-1]
    if not path or not path.startswith("/"):
        path = url.split("://")[-1].lstrip("/")
        path = "/" + path if not path.startswith("/") else path
    dst = f"{path}.bak-{datetime.now():%Y%m%d-%H%M%S}"
    try:
        shutil.copy(path, dst)
        return dst
    except Exception as e:  # pragma: no cover - best effort
        print(f"  (backup skipped: {e})")
        return None


async def main(commit: bool):
    async with engine.begin() as conn:
        before = {}
        for t in _TABLES:
            before[t] = (await conn.execute(text(f"SELECT count(*) FROM {t}"))).scalar()
        print("Rows to remove:")
        for t in _TABLES:
            print(f"  {t:22} {before[t]}")

        if not commit:
            print("\nDry run — nothing changed. Re-run with --yes to purge.")
            return

        for t in _TABLES:
            await conn.execute(text(f"DELETE FROM {t}"))
        # zero any supplier balances left dangling by removed on-credit POs
        await conn.execute(text("UPDATE suppliers SET balance = 0"))

        after = {t: (await conn.execute(text(f"SELECT count(*) FROM {t}"))).scalar() for t in _TABLES}
        print("\nAfter purge:")
        for t in _TABLES:
            print(f"  {t:22} {after[t]}")
    print("\nDone. Inventory is a clean slate; contacts were kept.")


if __name__ == "__main__":
    do = "--yes" in sys.argv
    if do:
        bak = _backup_sqlite()
        if bak:
            print(f"Backup written: {bak}\n")
    asyncio.run(main(do))
