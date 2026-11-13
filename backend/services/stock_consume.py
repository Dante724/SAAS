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

from sqlalchemy import select

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


def _layer_q(org_id, part_id, location_id, *, warehouse_id=None, live=True):
    """The part's cost layers at one location (audit INV-02: never another branch's), locked and
    read fresh. `warehouse_id` keeps them to that warehouse's layers plus the location's untagged ones."""
    from sqlalchemy import or_

    from backend.models import StockItemBatch
    q = select(StockItemBatch).where(StockItemBatch.org_id == org_id, StockItemBatch.part_id == part_id,
                                     StockItemBatch.location_id == location_id)
    if warehouse_id:
        q = q.where(or_(StockItemBatch.warehouse_id == warehouse_id, StockItemBatch.warehouse_id.is_(None)))
    if live:
        q = q.where(StockItemBatch.qty_remaining > 0)
    return q.with_for_update().execution_options(populate_existing=True)


async def _consume_layers(db, org_id, part_id, qty, location_id, *, fefo: bool = False,
                          warehouse_id=None, journal=None) -> tuple[float, float]:
    """FIFO-draw (FEFO with `fefo`) up to `qty` from the part's cost layers at `location_id`.
    Returns (units drawn, their total cost) — never more than the layers hold. `journal` (a job
    line) records each layer drawn on the line (`note_draw`), so a restore can refill exactly those.

    Audit INV-02: jobs drew layers org-wide while sales and write-offs drew the location's, so a
    job at branch A emptied branch B's layers and branch valuation/COGS drifted. The layers are
    locked (FOR UPDATE) and re-read, so two consumers can't both take the same units."""
    from backend.models import StockItemBatch
    if qty <= _EPS:
        return 0.0, 0.0
    q = _layer_q(org_id, part_id, location_id, warehouse_id=warehouse_id)
    if fefo:    # dated (perishable) stock first, undated last
        q = q.order_by(StockItemBatch.expiry_date.is_(None), StockItemBatch.expiry_date,
                       StockItemBatch.received_at, StockItemBatch.id)
    else:
        q = q.order_by(StockItemBatch.received_at, StockItemBatch.id)
    left, drawn, cost = Decimal(str(qty)), Decimal("0"), Decimal("0")
    for b in (await db.execute(q)).scalars().all():
        if left <= 0:
            break
        take = min(Decimal(str(b.qty_remaining or 0)), left)
        if take <= 0:
            continue
        b.qty_remaining = Decimal(str(b.qty_remaining or 0)) - take
        drawn += take
        cost += take * Decimal(str(b.unit_cost or 0))
        left -= take
        if journal is not None:
            note_draw(journal, b.id, location_id, float(take))
    return float(drawn), float(cost)


# ── INV-02: the per-line layer journal (order_lines.stock_draws, w4i01) ──────────────────────────
# A job line keeps {"layers": [{"batch_id", "location_id", "qty"}, ...]} in the order it drew them,
# like sale_lines.stock_draws. A restore takes from the end (the last drawn goes back first, the
# exact reverse of the draw) and refills those batches; a line with no journal (drawn before w4i01)
# or units no layer covered fall back to the newest layer with room.

def note_draw(line, batch_id: str, location_id: str, qty: float) -> None:
    """Record that `line` drew `qty` from layer `batch_id` (merged with its last entry when same)."""
    draws = dict(getattr(line, "stock_draws", None) or {})
    layers = [dict(d) for d in (draws.get("layers") or [])]
    if layers and layers[-1].get("batch_id") == batch_id and layers[-1].get("location_id") == location_id:
        layers[-1]["qty"] = round(float(layers[-1]["qty"]) + qty, 6)
    else:
        layers.append({"batch_id": batch_id, "location_id": location_id, "qty": round(qty, 6)})
    draws["layers"] = layers
    line.stock_draws = draws          # a new dict: the JSON column sees the change


async def _journal_lines(db, order_id, part_id, line=None) -> list:
    """The job's lines for this part with a journal: the named line first, then the most recently
    added. Read fresh, so a journal written by another request in this transaction is current."""
    from backend.models import OrderLine
    rows = list((await db.execute(select(OrderLine).where(
        OrderLine.order_id == order_id, OrderLine.ref_id == part_id, OrderLine.stock_draws.is_not(None))
        .execution_options(populate_existing=True))).scalars().all()) if order_id else []
    from datetime import datetime
    rows.sort(key=lambda ln: (ln.created_at or datetime.min, ln.id or ""), reverse=True)
    if line is not None:
        rows = [line] + [r for r in rows if r.id != line.id]
    return rows


async def _restore_from_journal(db, org_id, part_id, location_id, qty: Decimal, lines) -> Decimal:
    """Put up to `qty` back on the layers the lines' journals name at `location_id`, the last
    drawn first; each journal entry shrinks by what went back. Returns the units restored."""
    from backend.models import StockItemBatch
    done, settled = Decimal("0"), Decimal("0")
    for ln in lines:
        if settled >= qty:
            break
        layers = [dict(d) for d in ((ln.stock_draws or {}).get("layers") or [])]
        changed = False
        for d in reversed(layers):
            if settled >= qty:
                break
            if d.get("location_id") != location_id or float(d.get("qty") or 0) <= _EPS:
                continue
            want = min(Decimal(str(d["qty"])), qty - settled)
            b = (await db.execute(select(StockItemBatch).where(
                StockItemBatch.id == d["batch_id"], StockItemBatch.org_id == org_id,
                StockItemBatch.part_id == part_id, StockItemBatch.location_id == location_id)
                .with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
            back = Decimal("0")
            if b is not None:
                room = Decimal(str(b.qty_received or 0)) - Decimal(str(b.qty_remaining or 0))
                back = max(min(room, want), Decimal("0"))
                if back > 0:
                    b.qty_remaining = Decimal(str(b.qty_remaining or 0)) + back
            # the entry is settled either way: what had no room (a layer since refilled by
            # something else) or no layer (deleted) goes back newest-first, by the caller
            d["qty"] = round(float(Decimal(str(d["qty"])) - want), 6)
            done += back
            settled += want
            changed = True
        if changed:
            draws = dict(ln.stock_draws or {})
            draws["layers"] = [d for d in layers if float(d.get("qty") or 0) > _EPS]
            ln.stock_draws = draws
    return done


async def restore_layers(db, org_id, part_id, location_id, qty, *, warehouse_id=None,
                         order_id=None, line=None) -> float:
    """Put `qty` units back on the part's cost layers at `location_id` — the reverse of a draw.
    Returns the units restored (INV-02: only this location's layers, locked, so a line removed at
    branch A can't refill branch B).

    With `order_id` (and optionally the `line` giving units back), the layers the job's lines
    journalled go back first — exactly the ones they drew, the last drawn first. Whatever no journal
    covers goes on the newest layer first, never above what a layer received."""
    from backend.models import StockItemBatch
    left = Decimal(str(qty))
    if left <= 0:
        return 0.0
    exact = Decimal("0")
    if order_id or line is not None:
        lines = await _journal_lines(db, order_id or getattr(line, "order_id", None), part_id, line)
        if line is not None and line not in lines:
            lines = [line] + lines
        exact = await _restore_from_journal(db, org_id, part_id, location_id, left, lines)
        left -= exact
        if left <= 0:
            return float(exact)
    q = _layer_q(org_id, part_id, location_id, warehouse_id=warehouse_id, live=False).order_by(
        StockItemBatch.received_at.desc(), StockItemBatch.id.desc())
    done = Decimal("0")
    for b in (await db.execute(q)).scalars().all():
        if left <= 0:
            break
        room = Decimal(str(b.qty_received or 0)) - Decimal(str(b.qty_remaining or 0))
        if room <= 0:
            continue
        back = min(room, left)
        b.qty_remaining = Decimal(str(b.qty_remaining or 0)) + back
        left -= back
        done += back
    return float(done + exact)


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
    layered, layer_cost = await _consume_layers(db, org_id, part_id, taken, location_id, journal=line)
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
