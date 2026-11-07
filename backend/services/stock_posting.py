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
    create_batch: bool = True, set_last_cost: bool = False, expiry_date=None, lot: str | None = None,
    supplier_id: str | None = None, movement_type: str = "receive", warehouse_id: str | None = None,
    cost_known: bool = False,
) -> float:
    """Post `qty` of `part` into `location_id` at `unit_cost`. Returns the unit cost used.

    - Moving-average cost is always weighted against **global** on-hand (all locations),
      matching how `avg_cost` is a single per-part column.
    - A FIFO `StockItemBatch` layer is born per receipt (so exact-COGS fulfilment works),
      plus one `SerializedItem` per provided serial.
    - `set_last_cost` also refreshes `part.cost_price` (postings do; PO-receive leaves it).
    - `expiry_date` dates the cost layer for FEFO (expiry-tracked products).
    - Serials are expected already checked by `serial_intake.validate_intake`.
    - ii144c: no bin given → the location warehouse's default bin, when one is set. Each unit records
      its supplier and supplier-warranty end date (the product's supplier warranty from today).
    - `movement_type` names the audit row (a manual count-in is an `adjust`, not a `receive`);
      `warehouse_id` tags the movement and the cost layer (default: the stock row's warehouse).
    """
    from backend.services import stock_rows
    if bin_id:
        await stock_rows.check_bin(db, org_id, location_id, bin_id)
    else:
        bin_id = await stock_rows.default_bin(db, org_id, location_id)

    # Global moving-average: weight existing on-hand (across ALL locations) against the
    # incoming qty at this receipt's unit cost. Only move the average when a real cost is
    # supplied — a zero-cost line must not drag the average to zero — unless the caller says 0 is
    # the real cost (`cost_known`: a free PO line, opening stock entered at 0).
    if unit_cost or cost_known:
        on_hand = (await db.execute(select(func.coalesce(func.sum(StockLevel.qty_available), 0))
            .where(StockLevel.part_id == part.id))).scalar() or 0
        old_avg = part.avg_cost or part.cost_price or 0
        denom = on_hand + qty
        part.avg_cost = q4(((on_hand * old_avg) + (qty * unit_cost)) / denom) if denom else unit_cost
        if set_last_cost:
            part.cost_price = unit_cost

    # ii144a: the exact (product, location, bin) row — the old code reused the location's single row
    # and only tagged it with the first bin, so a later bin transfer made a second row and crashed here.
    row = await stock_rows.add(db, part.id, location_id, qty, bin_id=bin_id)
    # QA fix D: the movement and the cost layer say which warehouse the stock went into (they were
    # NULL for postings and PO receipts, so valuation showed a separate "—" warehouse).
    warehouse_id = warehouse_id or row.warehouse_id or await stock_rows.warehouse_for(db, location_id, bin_id)

    db.add(StockMovement(part_id=part.id, location_id=location_id, bin_id=bin_id,
                         movement_type=movement_type, qty=qty, unit_cost=unit_cost or None,
                         warehouse_id=warehouse_id,
                         created_by_id=created_by_id, ref_type=ref_type, ref_id=ref_id))

    if create_batch:
        from backend.models import SerializedItem, StockItemBatch
        batch = StockItemBatch(org_id=org_id, part_id=part.id, location_id=location_id,
                               warehouse_id=warehouse_id, bin_id=bin_id, po_id=ref_id if ref_type == "po" else None,
                               unit_cost=Decimal(str(unit_cost)), qty_received=Decimal(str(qty)),
                               qty_remaining=Decimal(str(qty)), expiry_date=expiry_date,
                               lot_number=(lot or "").strip()[:40] or None)
        db.add(batch)
        await db.flush()
        from datetime import timedelta

        from backend.models import _uuid
        from backend.services import serial_log
        from backend.services.dates import shop_today
        until = (await shop_today(db, org_id, location_id) + timedelta(days=int(part.supplier_warranty_days))
                 if part.supplier_warranty_days else None)
        for sn in (serials or [])[:int(qty)]:
            unit = SerializedItem(id=_uuid(), org_id=org_id, part_id=part.id, serial_number=sn, bin_id=bin_id,
                                  location_id=location_id, batch_id=batch.id,
                                  po_id=ref_id if ref_type == "po" else None, supplier_id=supplier_id,
                                  supplier_warranty_until=until,
                                  unit_cost=Decimal(str(unit_cost)), status="in_stock")
            db.add(unit)
            serial_log.record(db, unit, "received", ref_type=ref_type, ref_id=ref_id, actor_id=created_by_id)
    return unit_cost


async def apply_cost_adjustment(
    db, *, org_id: str, part: Part, add_cost_total: float,
    po_id: str | None = None, created_by_id: str | None = None, note: str | None = None,
    received_qty: float | None = None,
) -> dict:
    """144ap — landed-cost recost. Add `add_cost_total` (e.g. a freight bill that landed after
    the goods) to the inventory value of `part` WITHOUT changing quantity.

    With `po_id` + `received_qty` (the PO's units of this part received so far) the cost belongs
    to those units only (QA fix D): `add_cost_total / received_qty` per unit, landing on the PO's
    units still on hand (its FIFO layers). The share of units already sold or used is sunk COGS
    and is not pushed into inventory, and other stock (opening stock, other POs) is not touched.
    Without them: spread over everything on hand (the older behaviour).

    - The moving average (`avg_cost`) rises by what landed, over global on-hand.
    - The PO's remaining FIFO layers rise by the per-unit amount, so exact COGS carries it too.
    - A zero-qty `recost` StockMovement records it.
    Returns {applied, on_hand, expensed}: `applied` landed on inventory, `expensed` went to units
    already gone.
    """
    if not add_cost_total:
        return {"applied": 0.0, "on_hand": 0.0, "expensed": 0.0}
    from backend.models import StockItemBatch
    rows = (await db.execute(select(StockLevel).where(StockLevel.part_id == part.id))).scalars().all()
    on_hand = float(sum(r.qty_available or 0 for r in rows))
    if on_hand <= 0:
        return {"applied": 0.0, "on_hand": 0.0, "expensed": q2(add_cost_total)}   # nothing on hand to revalue
    loc_id = next((r.location_id for r in rows if (r.qty_available or 0) > 0), rows[0].location_id if rows else None)

    layers_q = (select(StockItemBatch).where(StockItemBatch.part_id == part.id, StockItemBatch.qty_remaining > 0)
                .with_for_update().execution_options(populate_existing=True))   # unit_cost += …: locked, fresh
    if po_id and received_qty and received_qty > 0:
        add_per_unit = add_cost_total / received_qty
        po_layers = (await db.execute(layers_q.where(StockItemBatch.po_id == po_id))).scalars().all()
        has_po_layers = (await db.execute(select(StockItemBatch.id).where(
            StockItemBatch.part_id == part.id, StockItemBatch.po_id == po_id).limit(1))).scalar_one_or_none()
        if has_po_layers:
            left = min(float(sum(float(b.qty_remaining or 0) for b in po_layers)), on_hand)
        else:      # received before cost layers existed: assume the PO's units are what's left first
            left = min(float(received_qty), on_hand)
        layers = po_layers
    else:
        add_per_unit = add_cost_total / on_hand
        left = on_hand
        layers = (await db.execute(layers_q.where(StockItemBatch.po_id == po_id) if po_id else layers_q)).scalars().all()
    applied = add_per_unit * left
    if applied:
        part.avg_cost = q4((part.avg_cost or part.cost_price or 0) + applied / on_hand)
    for b in layers:
        b.unit_cost = (b.unit_cost or Decimal("0")) + Decimal(str(q4(add_per_unit)))

    from backend.services import stock_rows
    db.add(StockMovement(part_id=part.id, location_id=loc_id, movement_type="recost", qty=0,
                         warehouse_id=await stock_rows.warehouse_for(db, loc_id) if loc_id else None,
                         unit_cost=q4(add_per_unit), created_by_id=created_by_id,
                         ref_type="po" if po_id else "recost", ref_id=po_id))
    return {"applied": q2(applied), "on_hand": on_hand, "expensed": q2(add_cost_total - applied)}


async def _atomic_add(db, obj, attr, delta, *, round_to=None):
    from backend.services.money import add_to_column
    return await add_to_column(db, obj, attr, delta, round_to=round_to)
