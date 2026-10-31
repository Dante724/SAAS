"""Serialized inventory & cost-layer engine (Mandate 2).

- consume_fifo_lifo: draw qty from StockItemBatch cost layers in FIFO or LIFO
  order, returning the exact cost of goods consumed (per-layer).
- allocate_serialized_item: atomically reserve one unique SerializedItem from a
  bin onto a work order using SELECT ... FOR UPDATE row-locking so two tickets
  can never grab the same physical unit (strict isolation, no race).
"""
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import StockItemBatch, SerializedItem

Q = Decimal("0.0001")


def _d(x) -> Decimal:
    return (Decimal(str(x or 0))).quantize(Q, rounding=ROUND_HALF_UP)


async def consume_fifo_lifo(db: AsyncSession, *, org_id: str, part_id: str,
                            qty: Decimal, method: str = "fifo") -> dict:
    """Consume `qty` of a part from its cost layers, oldest-first (FIFO) or
    newest-first (LIFO). Returns total cost + the per-layer breakdown. Each layer
    row is locked for update so concurrent consumers can't over-draw it."""
    qty = _d(qty)
    order_by = StockItemBatch.received_at.asc() if method == "fifo" else StockItemBatch.received_at.desc()
    # Lock candidate layers (skip already-emptied) for the duration of the txn.
    stmt = (select(StockItemBatch)
            .where(StockItemBatch.org_id == org_id, StockItemBatch.part_id == part_id,
                   StockItemBatch.qty_remaining > 0)
            .order_by(order_by).with_for_update())
    layers = (await db.execute(stmt)).scalars().all()

    remaining = qty
    total_cost = Decimal("0")
    breakdown = []
    for layer in layers:
        if remaining <= 0:
            break
        take = min(remaining, _d(layer.qty_remaining))
        if take <= 0:
            continue
        layer.qty_remaining = _d(layer.qty_remaining) - take
        cost = _d(take * _d(layer.unit_cost))
        total_cost += cost
        breakdown.append({"batch_id": layer.id, "qty": float(take),
                          "unit_cost": float(layer.unit_cost), "cost": float(cost)})
        remaining -= take

    if remaining > 0:
        # Not enough cost-layered stock — abort the whole transaction.
        raise HTTPException(409, f"Insufficient cost-layered stock: short by {float(remaining):g} unit(s)")

    return {"qty": float(qty), "total_cost": float(_d(total_cost)),
            "avg_unit_cost": float(_d(total_cost / qty)) if qty else 0, "layers": breakdown}


async def allocate_serialized_item(db: AsyncSession, *, org_id: str, part_id: str,
                                   order_id: str, bin_id: Optional[str] = None,
                                   serial_number: Optional[str] = None,
                                   location_id: Optional[str] = None,
                                   unbinned: bool = False) -> SerializedItem:
    """Atomically allocate one in-stock serialized unit onto an order.

    Uses with_for_update() to lock the candidate row(s); the first matching
    in-stock unit is flipped to allocated_to_order. Because the row is locked,
    a concurrent allocation will block until this txn commits, then see the new
    status and skip it — so the same physical unit can't be double-allocated.
    """
    conds = [SerializedItem.org_id == org_id, SerializedItem.part_id == part_id,
             SerializedItem.status == "in_stock"]
    if bin_id:
        conds.append(SerializedItem.bin_id == bin_id)
    elif unbinned:       # ii144c: stock taken from unbinned rows uses an unbinned unit
        conds.append(SerializedItem.bin_id.is_(None))
    if location_id:      # ii144c: a job only takes units at its own location
        conds.append(SerializedItem.location_id == location_id)
    if serial_number:
        from backend.services.serial_intake import matches as _sn_matches
        conds.append(_sn_matches(SerializedItem.serial_number, serial_number))

    stmt = (select(SerializedItem).where(*conds)
            .order_by(SerializedItem.created_at.asc())
            .limit(1).with_for_update(skip_locked=True))
    item = (await db.execute(stmt)).scalar_one_or_none()
    if not item:
        raise HTTPException(409, "No available serialized unit matches (out of stock or already allocated)")

    item.status = "allocated_to_order"
    item.order_id = order_id
    await db.flush()
    return item
