"""144at — reservation-aware stock consumption.

A reservation earmarks on-hand units to one order. `StockLevel.qty_reserved` is the sum of all
active holds at that location; `StockReservation` rows carry the per-order detail (order_id, qty,
expires_at, released). Consumption must respect this: a job may only take stock that is FREE
(on-hand minus everyone's reservations) plus its OWN reservation. Taking units reserved for a
*different* order is blocked unless the actor holds `can_override_reservation` — then it's drawn
from the other holds FIFO and audited.

This module centralises that rule so every consumer (work orders, POS sales) enforces it the
same way. Callers keep doing their own `qty_available` decrement; this only governs the
reservation bookkeeping and tells the caller how much (if any) is blocked.
"""
from datetime import datetime

from sqlalchemy import select

from backend.models import StockReservation
from backend.services.money import q4
from backend.services.dates import now_utc


async def _active_holds(db, part_id, location_id):
    now = now_utc()
    rows = (await db.execute(select(StockReservation).where(
        StockReservation.part_id == part_id, StockReservation.location_id == location_id,
        StockReservation.released == False))).scalars().all()  # noqa: E712
    return [r for r in rows if not (r.expires_at and r.expires_at < now)]


async def plan_consumption(db, *, part_id, location_id, order_id, qty):
    """Break a requested consumption into buckets without mutating anything.

    Returns dict with:
      avail          on-hand units
      reserved       total reserved (all orders)
      own            units this order has reserved
      phys_short     requested beyond what's physically on hand (→ normal pending/backorder)
      reserved_block units physically present but reserved to OTHER orders (→ needs override)
      free_take      units freely consumable now (free stock + this order's own reservation)
    """
    from backend.services import stock_rows
    avail, _held = await stock_rows.totals(db, part_id, location_id)
    holds = await _active_holds(db, part_id, location_id)
    # Reserved for the free-stock calc is the sum of *active* holds — expired holds self-heal and
    # never block, even before the release sweep touches StockLevel.qty_reserved.
    reserved = sum((r.qty or 0) for r in holds)
    own = sum((r.qty or 0) for r in holds if order_id and r.order_id == order_id)
    free = max(avail - reserved, 0)
    phys_take = min(qty, max(avail, 0))
    phys_short = qty - phys_take
    free_take = min(phys_take, free + own)
    reserved_block = phys_take - free_take
    return {"avail": avail, "reserved": reserved, "own": own, "free": free,
            "phys_short": phys_short, "free_take": free_take,
            "reserved_block": q4(reserved_block)}


async def draw(db, *, org_id, part_id, location_id, order_id, own_qty=0.0, others_qty=0.0,
               actor_id=None, taken_label=""):
    """Release reservations for consumed units: `own_qty` from this order's own holds, and
    `others_qty` from OTHER orders' holds (FIFO, audited — the admin-override path). Decrements
    `StockLevel.qty_reserved` per held unit and releases holds as they empty. Does NOT touch
    `qty_available` (the caller owns that). Returns {drew_own, drew_others, overrides}.
    """
    from backend.models import StockMovement
    from backend.services import stock_rows
    holds = await _active_holds(db, part_id, location_id)
    holds.sort(key=lambda r: r.created_at or datetime.min)
    released = 0.0

    def _take(hold, want):
        nonlocal released
        take = min(hold.qty or 0, want)
        hold.qty = (hold.qty or 0) - take
        released += take
        if (hold.qty or 0) <= 1e-9:
            hold.released = True
        return take

    drew_own = 0.0
    rem = own_qty
    for h in [x for x in holds if order_id and x.order_id == order_id]:
        if rem <= 1e-9:
            break
        t = _take(h, rem); drew_own += t; rem -= t

    drew_others = 0.0
    overrides = []
    rem = others_qty
    for h in [x for x in holds if not (order_id and x.order_id == order_id)]:
        if rem <= 1e-9:
            break
        from_order = h.order_id
        t = _take(h, rem)
        if t > 0:
            drew_others += t
            overrides.append({"order_id": from_order, "qty": q4(t), "hold_id": h.id})
            rem -= t

    if released > 0:      # ii144a: reserved lives on the location's rows (one per bin)
        await stock_rows.reserve(db, part_id, location_id, -released)
    if overrides:
        # Audit trail: a zero-qty movement records the override (who + which job it was taken for).
        db.add(StockMovement(part_id=part_id, location_id=location_id, qty=0,
                             movement_type="reserve_override", ref_type="order", ref_id=order_id,
                             created_by_id=actor_id))
    return {"drew_own": q4(drew_own), "drew_others": q4(drew_others), "overrides": overrides}


async def guard_and_draw(db, *, org_id, part_id, location_id, order_id, qty, actor_id,
                         allow_override, taken_label=""):
    """One-call helper for a consumer: given a requested `qty`, work out how much is free, own,
    reserved-to-others and physically short, block when others' stock is needed without override,
    otherwise release the right holds. Returns:
      take        units to physically deduct from qty_available now (free + own + overridden)
      pending     units truly not on hand (normal backorder)
      blocked     units reserved to other orders that override would be needed for (0 when allowed)
      overrides   audit detail of other orders drawn from
    When `blocked > 0` the caller should raise (nothing was mutated)."""
    p = await plan_consumption(db, part_id=part_id, location_id=location_id, order_id=order_id, qty=qty)
    phys_take = min(qty, max(p["avail"], 0))
    pending = qty - phys_take
    over_free = max(phys_take - p["free"], 0)
    own_consume = min(over_free, p["own"])
    others_block = q4(over_free - own_consume)
    if others_block > 1e-9 and not allow_override:
        return {"take": 0, "pending": 0, "blocked": others_block, "overrides": []}
    res = await draw(db, org_id=org_id, part_id=part_id, location_id=location_id, order_id=order_id,
                     own_qty=own_consume, others_qty=others_block if allow_override else 0,
                     actor_id=actor_id, taken_label=taken_label)
    return {"take": q4(phys_take), "pending": q4(pending), "blocked": 0,
            "overrides": res["overrides"]}
