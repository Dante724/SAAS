"""Stock quantities survive concurrent updates (security phase 4, audit B18).

Stock counts were read-modify-write (`sl.qty_available -= qty`) in ~20 places, so two requests
touching the same part/location at once lost one update — phantom or vanished stock.
"""
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.models import Location, Organisation, Part, StockLevel
from backend.services.stock_posting import apply_receipt_line


@pytest.mark.asyncio
async def test_concurrent_receipts_do_not_lose_stock(client, tenant, db):
    org_id = (await db.execute(select(Organisation))).scalars().first().id
    loc_id = (await db.execute(select(Location).where(Location.org_id == org_id))).scalars().first().id
    part = Part(org_id=org_id, name="Battery", cost_price=5, avg_cost=5)
    db.add(part)
    await db.commit()
    part_id = part.id
    await apply_receipt_line(db, org_id=org_id, part=part, location_id=loc_id, qty=1, unit_cost=5)
    await db.commit()                                  # row exists with 1
    # session A holds its copy (the identity map is weak — an unreferenced row would just reload)
    stale = (await db.execute(select(StockLevel).where(StockLevel.part_id == part_id))).scalar_one()

    other = async_sessionmaker(db.bind, expire_on_commit=False)
    async with other() as b:                           # session B receives 4
        bpart = (await b.execute(select(Part).where(Part.id == part_id))).scalar_one()
        await apply_receipt_line(b, org_id=org_id, part=bpart, location_id=loc_id, qty=4, unit_cost=5)
        await b.commit()
    await apply_receipt_line(db, org_id=org_id, part=part, location_id=loc_id, qty=10, unit_cost=5)  # A, stale
    await db.commit()
    async with other() as check:
        qty = (await check.execute(select(StockLevel.qty_available).where(
            StockLevel.part_id == part_id, StockLevel.location_id == loc_id))).scalar_one()
    assert float(qty) == 15.0 and float(stale.qty_available) == 15.0, qty
