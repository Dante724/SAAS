"""Read-only audit: find rows that point at something that no longer exists ("orphans").

Why: some foreign keys the models declare were never created by a migration, so production does
not enforce them (see KNOWN_MISSING_FKS in backend/tests/test_144eo_migrated_schema.py). Before a
migration can add them, existing orphan rows must be found and fixed — otherwise adding the
constraint fails on deploy.

It checks EVERY foreign key the models declare and, for each, reports how many orphan rows exist
and whether the database actually enforces that FK.

SAFE ON PRODUCTION: only SELECTs; on Postgres the transaction is READ ONLY; always rolled back.

Usage (uses the app's DATABASE_URL, e.g. postgresql+asyncpg://… or sqlite+aiosqlite:///…):
    DATABASE_URL=... python scripts/check_fk_orphans.py
Exit code: 0 = no orphans anywhere, 1 = orphans found (details printed), 2 = could not run.
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.disable(logging.CRITICAL)

from sqlalchemy import inspect, text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from backend.db.database import Base  # noqa: E402
from backend.models import all_models  # noqa: E402,F401 — registers every table


def _enforced_fks(sync_conn) -> set[tuple[str, str]]:
    insp = inspect(sync_conn)
    out = set()
    for t in insp.get_table_names():
        for fk in insp.get_foreign_keys(t):
            for c in fk["constrained_columns"]:
                out.add((t, c))
    return out


async def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("Set DATABASE_URL (the app's database URL).")
        return 2
    engine = create_async_engine(url)
    q = engine.dialect.identifier_preparer.quote
    findings, checked, skipped = [], 0, []
    try:
        async with engine.connect() as conn:
            if engine.dialect.name == "postgresql":
                await conn.execute(text("SET TRANSACTION READ ONLY"))
            existing = set(await conn.run_sync(lambda c: inspect(c).get_table_names()))
            enforced = await conn.run_sync(_enforced_fks)
            for table in Base.metadata.sorted_tables:
                for fk in sorted(table.foreign_keys, key=lambda f: f.parent.name):
                    child, col = table.name, fk.parent.name
                    parent, pcol = fk.column.table.name, fk.column.name
                    if child not in existing or parent not in existing:
                        skipped.append(f"{child}.{col}")
                        continue
                    sql = (f"SELECT COUNT(*) FROM {q(child)} c WHERE c.{q(col)} IS NOT NULL AND NOT EXISTS "
                           f"(SELECT 1 FROM {q(parent)} p WHERE p.{q(pcol)} = c.{q(col)})")
                    try:
                        n = (await conn.execute(text(sql))).scalar() or 0
                    except Exception as e:  # noqa: BLE001 — e.g. column missing in this DB
                        skipped.append(f"{child}.{col} ({type(e).__name__})")
                        await conn.rollback()
                        continue
                    checked += 1
                    if n:
                        findings.append((child, col, parent, pcol, n, (child, col) in enforced))
            await conn.rollback()  # never leave anything behind
    finally:
        await engine.dispose()

    print(f"Checked {checked} foreign keys declared by the models.")
    if skipped:
        print(f"Skipped {len(skipped)} (table/column not in this database): {', '.join(skipped)}")
    if not findings:
        print("✓ No orphan rows. Safe to add the missing foreign keys.")
        return 0
    print(f"\n✗ {len(findings)} foreign key(s) have orphan rows:")
    for child, col, parent, pcol, n, is_enforced in findings:
        note = "" if is_enforced else "   <- not enforced by the database"
        print(f"  {child}.{col} -> {parent}.{pcol}: {n} orphan row(s){note}")
    print("\nFix these rows (re-point or NULL them) before adding the constraints.")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
