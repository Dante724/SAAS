"""One way to write stock off (QA fix D, 31 Oct 2026).

There were two write-off paths and they disagreed: the stock adjust (`movement_type=writeoff`) took
the units off the shelf but left their FIFO cost layers full, and silently cut a write-off larger than
the stock down to what was there; the catalog write-off consumed the layers but logged the movement
as +qty. Both now come here:

- more than is on hand at the location is refused (409), nothing moves;
- the units leave the location's rows (`stock_rows.draw`, the named bin first);
- the same units leave the cost layers at that location — FEFO for expiry-tracked products, else
  FIFO — so the layers keep holding exactly what is on hand;
- one movement per bin taken, with a negative qty (outflow, like sales and stock documents) and the
  unit cost of what went.
Callers commit.
"""
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select

from backend.models import Part, StockItemBatch, StockMovement
from backend.services import stock_rows

_EPS = 1e-9


async def consume_layers(db, *, part: Part, location_id: str, qty: float,
                         batch_id: Optional[str] = None, taken: Optional[list] = None) -> float:
    """Take `qty` out of the part's cost layers at the location; returns what those units cost.
    `batch_id` (a serialized unit's own layer) goes first. Units no layer covers — stock that
    predates cost layers — are valued at the average cost. `taken`, when given, collects
    {"batch_id", "qty"} per layer drawn (so a voided sale can put them back)."""
    q = select(StockItemBatch).where(StockItemBatch.part_id == part.id,
                                     StockItemBatch.location_id == location_id,
                                     StockItemBatch.qty_remaining > 0)
    q = q.order_by(StockItemBatch.expiry_date.asc().nullslast() if part.expiry_tracking
                   else StockItemBatch.received_at.asc())
    # Lock the layers and read them fresh: two write-offs at once each set qty_remaining from what
    # they read, so without the lock (Postgres) or the refresh (a layer this session already holds)
    # one decrement was lost.
    q = q.with_for_update().execution_options(populate_existing=True)
    layers = list((await db.execute(q)).scalars().all())
    if batch_id:
        layers.sort(key=lambda b: b.id != batch_id)
    left, cost = float(qty), 0.0
    for b in layers:
        if left <= _EPS:
            break
        take = min(float(b.qty_remaining or 0), left)
        if take <= _EPS:
            continue
        b.qty_remaining = Decimal(str(b.qty_remaining or 0)) - Decimal(str(take))
        if taken is not None:
            taken.append({"batch_id": b.id, "qty": take})
        cost += take * float(b.unit_cost or 0)
        left -= take
    if left > _EPS:
        cost += left * float(part.avg_cost or part.cost_price or 0)
    return cost


async def write_off(db, *, part: Part, location_id: str, qty: float, created_by_id: Optional[str] = None,
                    movement_type: str = "writeoff", ref_type: Optional[str] = "writeoff",
                    ref_id: Optional[str] = None, warehouse_id: Optional[str] = None,
                    bin_id: Optional[str] = None, batch_id: Optional[str] = None,
                    whole_check: bool = True) -> dict:
    """Write `qty` (> 0) of `part` off at `location_id`. Returns {qty, cost, qty_available}.
    `whole_check=False` lets an archive clear out a fractional count left by older data."""
    qty = float(qty)
    if qty <= _EPS:
        raise HTTPException(422, "Provide a positive quantity to write off")
    if whole_check:
        stock_rows.check_whole(part, qty)
    try:
        taken = await stock_rows.draw(db, part.id, location_id, qty, bin_id=bin_id)
    except stock_rows.NotEnoughStock as e:
        raise HTTPException(409, f"Only {e.have:g} of “{part.name}” in stock at this location — "
                                 f"can't write off {qty:g}. If the shelf has more, count it first (Stocktake).")
    cost = await consume_layers(db, part=part, location_id=location_id, qty=qty, batch_id=batch_id)
    unit = cost / qty
    warehouse_id = warehouse_id or await stock_rows.warehouse_for(db, location_id)
    for t_bin, t_qty in taken:
        db.add(StockMovement(part_id=part.id, location_id=location_id, bin_id=t_bin,
                             warehouse_id=warehouse_id, movement_type=movement_type, qty=-t_qty,
                             unit_cost=round(unit, 2), ref_type=ref_type, ref_id=ref_id,
                             created_by_id=created_by_id))
    return {"qty": qty, "cost": round(cost, 2),
            "qty_available": (await stock_rows.totals(db, part.id, location_id))[0]}


async def write_off_units(db, *, part: Part, location_id: str, serials, created_by_id: Optional[str] = None,
                          movement_type: str = "writeoff", ref_type: Optional[str] = "writeoff",
                          ref_id: Optional[str] = None, warehouse_id: Optional[str] = None,
                          label: Optional[str] = None) -> dict:
    """QA F2 — write off named units of a serialized product at `location_id`: each comes out of its
    own bin and cost layer and is marked written off, so the units always match the stock count."""
    from backend.models import SerializedItem
    from backend.services import serial_log
    from backend.services.serial_intake import matches as _sn_matches
    named = list(dict.fromkeys(x.strip() for x in (serials or []) if x and str(x).strip()))
    if not named:
        raise HTTPException(422, f"Scan the IMEI / serial of each “{part.name}” to write off")
    units = []
    for sn in named:
        u = (await db.execute(select(SerializedItem).where(
            SerializedItem.org_id == part.org_id, SerializedItem.part_id == part.id,
            SerializedItem.status == "in_stock", SerializedItem.location_id == location_id,
            _sn_matches(SerializedItem.serial_number, sn)).with_for_update())).scalars().first()
        if u is None:
            raise HTTPException(409, f"{sn} isn't in stock here")
        units.append(u)
    cost, out = 0.0, None
    for u in units:
        out = await write_off(db, part=part, location_id=location_id, qty=1, created_by_id=created_by_id,
                              movement_type=movement_type, ref_type=ref_type, ref_id=ref_id,
                              warehouse_id=warehouse_id, bin_id=u.bin_id, batch_id=u.batch_id)
        cost += out["cost"]
        u.status = "written_off"
        serial_log.record(db, u, "written_off", ref_type=ref_type, ref_id=ref_id,
                          label=label or "Written off", actor_id=created_by_id)
    return {"qty": float(len(units)), "cost": round(cost, 2), "qty_available": out["qty_available"],
            "serials": [u.serial_number for u in units]}

