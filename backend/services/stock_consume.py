"""One way to take stock for a job line (stock integrity pass, 2026-10-30).

Every path that consumes on-hand units for a work-order product line — creating the job, adding or
merging a line, raising its qty, converting an estimate, writing off pending stock, and PO auto-fill
— calls `consume_for_line`. It:

- locks the location's stock rows (``FOR UPDATE`` where the database supports it) and draws with
  ``allow_short=False``, so two jobs racing for the last unit can't both take it — whatever could
  not be drawn is returned to the caller, who books it as pending;
- logs one negative "use" movement for what was actually drawn;
- draws the FIFO cost layers for the drawn units (never more than the layers hold, so a short layer
  set can't half-consume and then abort) and folds their cost into the line's unit cost, weighted
  with the units the line already carried;
- allocates in-stock serialized units onto the order.

Reservation bookkeeping (whose holds the units come from) stays with the caller, who decides how
much may be taken before calling this.
"""
from decimal import Decimal

from sqlalchemy import func, select

from backend.services import stock_rows

_EPS = 1e-9


async def lock_rows(db, part_id: str, location_id: str):
    """Lock the (product, location) rows for the rest of the transaction."""
    return await stock_rows.rows(db, part_id, location_id, lock=True)


async def _draw_what_is_there(db, part_id, location_id, qty) -> float:
    try:
        await stock_rows.draw(db, part_id, location_id, qty)
        return qty
    except stock_rows.NotEnoughStock as e:
        have = min(max(e.have, 0.0), qty)
        if have <= _EPS:
            return 0.0
        try:
            await stock_rows.draw(db, part_id, location_id, have)
            return have
        except stock_rows.NotEnoughStock:
            return 0.0


async def _consume_layers(db, org_id, part_id, qty) -> tuple[float, float]:
    """FIFO-draw up to `qty` from the part's cost layers. Returns (units drawn, their total cost)."""
    from backend.models import StockItemBatch
    from backend.services.serialized import consume_fifo_lifo
    layered = (await db.execute(select(func.coalesce(func.sum(StockItemBatch.qty_remaining), 0)).where(
        StockItemBatch.org_id == org_id, StockItemBatch.part_id == part_id,
        StockItemBatch.qty_remaining > 0))).scalar_one()
    take = min(Decimal(str(qty)), Decimal(str(layered or 0)))
    if take <= 0:
        return 0.0, 0.0
    res = await consume_fifo_lifo(db, org_id=org_id, part_id=part_id, qty=take, method="fifo")
    return float(take), float(res["total_cost"])


async def _allocate_serials(db, org_id, part_id, order_id, n: int) -> None:
    from fastapi import HTTPException

    from backend.services.serialized import allocate_serialized_item
    for _ in range(max(n, 0)):
        try:
            await allocate_serialized_item(db, org_id=org_id, part_id=part_id, order_id=order_id)
        except HTTPException:
            break          # not a serialized part (or none left) — fine


async def consume_for_line(db, *, org_id: str, order_id: str, line, location_id: str, qty: float,
                           part_id: str | None = None, alert: bool = True) -> float:
    """Take up to `qty` units for `line` (whose `qty` already includes them). Returns the units
    actually drawn; the caller books `qty - drawn` as pending."""
    from backend.models import StockMovement
    part_id = part_id or line.ref_id
    if not part_id or qty <= _EPS:
        return 0.0
    await lock_rows(db, part_id, location_id)
    taken = await _draw_what_is_there(db, part_id, location_id, qty)
    if taken <= _EPS:
        return 0.0
    db.add(StockMovement(part_id=part_id, location_id=location_id, movement_type="use",
                         qty=-taken, ref_type="order", ref_id=order_id))
    layered, layer_cost = await _consume_layers(db, org_id, part_id, taken)
    if layered > _EPS:
        old_cost = float(line.cost_price or 0)
        new_unit = (layer_cost + (taken - layered) * old_cost) / taken
        total_units = float(line.qty or 0)
        if total_units > _EPS:
            # the line's other units keep their cost; only the units drawn now take the layer cost
            line.cost_price = round((old_cost * max(total_units - taken, 0) + new_unit * taken)
                                    / max(total_units, taken), 4)
        else:
            line.cost_price = round(new_unit, 4)
    await _allocate_serials(db, org_id, part_id, order_id, int(taken))
    if alert:
        from backend.services import inventory_alerts as _alerts  # 144bo: low-stock alert
        await _alerts.maybe_low_stock_by_id(db, org_id, part_id)
    return taken
