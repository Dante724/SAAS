"""Single source of truth for stock inflow.

Every path that brings stock in — manual postings, PO receipts, (later) returns and
transfers — routes one line through `apply_receipt_line` so quantity, movement audit,
FIFO cost layer, and moving-average cost are computed *the same way every time*. Before
this existed, postings and PO-receive each did their own thing and disagreed on the WAC
basis and on whether a cost layer was created.
"""
from decimal import Decimal

from sqlalchemy import func, select

from backend.models import Part, StockLevel, StockMovement
from backend.services.money import q2, q4


async def apply_receipt_line(
    db, *, org_id: str, part: Part, location_id: str, qty: float, unit_cost: float,
    created_by_id: str | None = None, ref_type: str = "posting", ref_id: str | None = None,
    bin_id: str | None = None, serials: list[str] | None = None,
    create_batch: bool = True, set_last_cost: bool = False, expiry_date=None,
) -> float:
    """Post `qty` of `part` into `location_id` at `unit_cost`. Returns the unit cost used.

    - Moving-average cost is always weighted against **global** on-hand (all locations),
      matching how `avg_cost` is a single per-part column.
    - A FIFO `StockItemBatch` layer is born per receipt (so exact-COGS fulfilment works),
      plus one `SerializedItem` per provided serial.
    - `set_last_cost` also refreshes `part.cost_price` (postings do; PO-receive leaves it).
    - `expiry_date` dates the cost layer for FEFO (expiry-tracked products).
    - Serials are expected already checked by `serial_intake.validate_intake`.
    """
    from backend.services import stock_rows
    if bin_id:
        await stock_rows.check_bin(db, org_id, location_id, bin_id)

    # Global moving-average: weight existing on-hand (across ALL locations) against the
    # incoming qty at this receipt's unit cost. Only move the average when a real cost is
    # supplied — a zero-cost line must not drag the average to zero.
    if unit_cost:
        on_hand = (await db.execute(select(func.coalesce(func.sum(StockLevel.qty_available), 0))
            .where(StockLevel.part_id == part.id))).scalar() or 0
        old_avg = part.avg_cost or part.cost_price or 0
        denom = on_hand + qty
        part.avg_cost = q4(((on_hand * old_avg) + (qty * unit_cost)) / denom) if denom else unit_cost
        if set_last_cost:
            part.cost_price = unit_cost

    # ii144a: the exact (product, location, bin) row — the old code reused the location's single row
    # and only tagged it with the first bin, so a later bin transfer made a second row and crashed here.
    await stock_rows.add(db, part.id, location_id, qty, bin_id=bin_id)

    db.add(StockMovement(part_id=part.id, location_id=location_id, bin_id=bin_id,
                         movement_type="receive", qty=qty, unit_cost=unit_cost or None,
                         created_by_id=created_by_id, ref_type=ref_type, ref_id=ref_id))

    if create_batch:
        from backend.models import SerializedItem, StockItemBatch
        batch = StockItemBatch(org_id=org_id, part_id=part.id, location_id=location_id,
                               bin_id=bin_id, po_id=ref_id if ref_type == "po" else None,
                               unit_cost=Decimal(str(unit_cost)), qty_received=Decimal(str(qty)),
                               qty_remaining=Decimal(str(qty)), expiry_date=expiry_date)
        db.add(batch)
        await db.flush()
        for sn in (serials or [])[:int(qty)]:
            db.add(SerializedItem(org_id=org_id, part_id=part.id, serial_number=sn, bin_id=bin_id,
                                  location_id=location_id, batch_id=batch.id,
                                  po_id=ref_id if ref_type == "po" else None,
                                  unit_cost=Decimal(str(unit_cost)), status="in_stock"))
    return unit_cost


async def apply_cost_adjustment(
    db, *, org_id: str, part: Part, add_cost_total: float,
    po_id: str | None = None, created_by_id: str | None = None, note: str | None = None,
) -> dict:
    """144ap — landed-cost recost. Add `add_cost_total` (e.g. a freight bill that landed after
    the goods) to the inventory value of `part` WITHOUT changing quantity.

    - Bumps the global moving-average (`avg_cost`) by the on-hand share only — value tied to
      units already consumed can't be pushed back into inventory (it's sunk COGS), matching how
      lightweight inventory systems recost: only stock still on hand is revalued.
    - Raises the remaining FIFO layers (this PO's first, else the part's) so exact-COGS
      fulfilment carries the freight too.
    - Writes a zero-qty `recost` StockMovement for the audit trail.
    Returns {applied, on_hand} — `applied` is the portion that actually landed on inventory.
    """
    if not add_cost_total:
        return {"applied": 0.0, "on_hand": 0.0}
    rows = (await db.execute(select(StockLevel).where(StockLevel.part_id == part.id))).scalars().all()
    on_hand = float(sum(r.qty_available or 0 for r in rows))
    if on_hand <= 0:
        return {"applied": 0.0, "on_hand": 0.0}   # nothing on hand to revalue
    loc_id = next((r.location_id for r in rows if (r.qty_available or 0) > 0), rows[0].location_id if rows else None)
    # Moving average carries the full added cost spread over what's still on hand.
    part.avg_cost = q4((part.avg_cost or part.cost_price or 0) + add_cost_total / on_hand)

    # Raise remaining FIFO layers by the same per-unit amount (prefer this PO's own layers).
    from backend.models import StockItemBatch
    add_per_unit = add_cost_total / on_hand
    q = select(StockItemBatch).where(StockItemBatch.part_id == part.id, StockItemBatch.qty_remaining > 0)
    if po_id:
        q = q.where(StockItemBatch.po_id == po_id)
    for b in (await db.execute(q)).scalars().all():
        b.unit_cost = (b.unit_cost or Decimal("0")) + Decimal(str(q4(add_per_unit)))

    db.add(StockMovement(part_id=part.id, location_id=loc_id, movement_type="recost", qty=0,
                         unit_cost=q4(add_per_unit), created_by_id=created_by_id,
                         ref_type="po" if po_id else "recost", ref_id=po_id))
    return {"applied": q2(add_cost_total), "on_hand": on_hand}


async def _atomic_add(db, obj, attr, delta, *, round_to=None):
    from backend.services.money import add_to_column
    return await add_to_column(db, obj, attr, delta, round_to=round_to)
