"""When a product may be archived (QA fix D, 31 Oct 2026).

Archiving hides a product from the catalog, the till and every stock report — so a product that
still has units on the shelf would take that stock (and its value) off the books. Every archive
path asks here first: the inventory bulk action, the product edit and the catalog bulk delete.

Rather than send the user off to write the stock off by hand, each path also takes
`write_off_remaining`: the remaining units are written off (cost layers and all, through
services/stock_writeoff) and the product is archived in the same step.
"""
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select

from backend.models import Part, StockLevel


async def archive_blocker(db, org_id: str, part: Part) -> Optional[str]:
    """Why `part` can't be archived yet, or None when it can. The one rule is
    product_codes.archive_blocker: still in stock, or on an open job, estimate or purchase order."""
    from backend.services.product_codes import archive_blocker as _blocker
    return await _blocker(db, org_id, part)


async def write_off_remaining(db, current, part: Part) -> float:
    """Write off everything `part` still has on hand, location by location, so it can be archived.
    Refused (409) while some of it is reserved for a job — that stock is spoken for. Returns the
    quantity written off. The caller commits."""
    from backend.models import SerializedItem
    from backend.services import serial_log, stock_writeoff
    from backend.services import warehouses as _wh
    per_loc = (await db.execute(select(
        StockLevel.location_id, func.sum(StockLevel.qty_available), func.sum(StockLevel.qty_reserved))
        .where(StockLevel.part_id == part.id).group_by(StockLevel.location_id))).all()
    reserved = sum(float(r or 0) for _l, _a, r in per_loc)
    if reserved > 1e-9:
        raise HTTPException(409, f"{reserved:g} of “{part.name}” are reserved for a job. "
                                 "Release them on the job first, then archive.")
    total = 0.0
    for loc_id, avail, _res in per_loc:
        avail = float(avail or 0)
        if avail <= 1e-9:
            continue
        w = await _wh.guard_action(db, current, "writeoff", location_id=loc_id)
        if part.serialized:     # the units themselves leave too
            for unit in (await db.execute(select(SerializedItem).where(
                    SerializedItem.part_id == part.id, SerializedItem.location_id == loc_id,
                    SerializedItem.status == "in_stock"))).scalars().all():
                unit.status = "written_off"
                serial_log.record(db, unit, "written_off", ref_type="archive",
                                  label="Written off on archive", actor_id=current.id)
        await stock_writeoff.write_off(db, part=part, location_id=loc_id, qty=avail, created_by_id=current.id,
                                       ref_type="archive", warehouse_id=w.id if w else None, whole_check=False)
        total += avail
    return total
