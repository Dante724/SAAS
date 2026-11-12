"""Inter-branch transfers with transit statuses (Orderry Mar-2026 model).

State machine and stock semantics:
  draft       editable, no stock effect
  ready       source qty_available -= qty: the units come off the shelf and are counted once
              (not also held as reserved); logged as transfer_out
  in_transit  no stock change; goods now ride ON the transfer (virtual in-transit)
  received    arrived at destination, still held for verification (no change)
  completed   destination qty_available += what arrived (goods land)
  canceled    before shipping: the units go back on the source shelf;
              after shipping: caller names the warehouse that takes goods back.
A transfer made "ready" before the count-once fix held its units as reserved too and logged
nothing until it shipped; those still release the hold the old way (legacy_ready below).
Every hop writes StockMovements and a feed event. Mutations require
can_manage_inventory."""
from datetime import timedelta
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import (
    Employee,
    Location,
    Part,
    SerializedItem,
    StockMovement,
    StockTransfer,
    StockTransferLine,
)
from backend.services.auth import get_current_employee, require_perm
from backend.services import serial_log, stock_rows
from backend.services.batching import names_by_id, rows_by
from backend.services.numbering import next_number
from backend.services.money import add_to_column, claim_status, q2
from backend.services.dates import now_utc
from backend.services.owned import require_location, require_owned_ids

router = APIRouter(prefix="/api/transfers", tags=["transfers"])

ORDERED = ["draft", "ready", "in_transit", "received", "completed"]


async def _lines(db, tid):
    return (await db.execute(select(StockTransferLine).where(
        StockTransferLine.transfer_id == tid))).scalars().all()


# ── QA F2: serialized units travel with the box ─────────────────────────────────────────────────
# A serialized line names its units (serial_ids). Ready: they leave the shelf ("in_transit", drawn
# from their own bins). Completed: the ones that arrived are in stock at the destination, a missing
# one is written off. Canceled: they go back in stock where the goods go back.

async def _box_units(db, org_id: str, ids, *, lock: bool = False) -> list:
    if not ids:
        return []
    q = select(SerializedItem).where(SerializedItem.org_id == org_id, SerializedItem.id.in_(list(ids)))
    if lock:
        q = q.with_for_update().execution_options(populate_existing=True)
    return list((await db.execute(q)).scalars().all())


async def _pick_units(db, org_id: str, part: Part, location_id: str, serials, qty: float) -> list:
    """The in-stock units at the source a new serialized line names (one per unit)."""
    from backend.services.serial_intake import matches as _sn_matches
    named = list(dict.fromkeys(x.strip() for x in (serials or []) if x and x.strip()))
    if not part.serialized:
        if named:
            raise HTTPException(422, f"“{part.name}” isn't tracked by serial number")
        return []
    if abs(qty - round(qty)) > 1e-9 or len(named) != int(round(qty)):
        raise HTTPException(422, f"Scan the IMEI / serial of each “{part.name}” in the box "
                                 f"({len(named)} of {qty:g} entered)")
    units = []
    for sn in named:
        u = (await db.execute(select(SerializedItem).where(
            SerializedItem.org_id == org_id, SerializedItem.part_id == part.id,
            SerializedItem.status == "in_stock", SerializedItem.location_id == location_id,
            _sn_matches(SerializedItem.serial_number, sn)))).scalars().first()
        if u is None:
            raise HTTPException(409, f"{sn} isn't in stock at the source")
        units.append(u)
    return units


def _place(u, location_id, warehouse_id, status="in_stock"):
    u.location_id, u.warehouse_id, u.bin_id, u.status = location_id, warehouse_id, None, status


async def _serials_by_line(db, org_id: str, lines) -> dict:
    ids = {i for ln in lines for i in (ln.serial_ids or [])}
    sn = {u.id: u.serial_number for u in await _box_units(db, org_id, ids)}
    return {ln.id: [sn[i] for i in (ln.serial_ids or []) if i in sn] for ln in lines}


# ── council M3: the FIFO cost layers travel with the box ───────────────────────────────────────
# "ready" takes the units off the source's layers (each serialized unit's own layer first, the rest
# FIFO / FEFO) and writes one transfer_out movement per layer drawn, carrying that layer's unit
# cost. "completed" lands what arrived on new layers at the destination at those costs (oldest
# first; a shortfall is the loss already booked). A cancel puts them back where the goods go: onto
# the layers they came off when there's room, else on new layers. Those per-layer movements are
# the record (no schema change); they carry created_by_id, which the single per-line movement an
# older transfer wrote never did — such a transfer's layers were never drawn, so they're drawn
# from the source when it ships on.

async def _parts(db, org_id: str, lines) -> dict:
    ids = {l.part_id for l in lines}
    return {p.id: p for p in (await db.execute(select(Part).where(
        Part.org_id == org_id, Part.id.in_(ids)))).scalars()} if ids else {}


async def _lock_rows(db, lines, *location_ids) -> None:
    """Lock order: every StockLevel row first, sorted by (part_id, location_id), then layers."""
    for pid, loc in sorted({(l.part_id, loc) for l in lines for loc in location_ids if loc}):
        await stock_rows.rows(db, pid, loc, lock=True)


async def _take_layers(db, part: Part, location_id: str, qty: float, units) -> list:
    """Draw `qty` off the location's layers; [(qty, unit_cost)] per layer drawn, in order. Units no
    layer covers come last at the average cost. Each unit's cost is set to its layer's."""
    from backend.models import StockItemBatch
    from backend.services.stock_writeoff import consume_layers
    taken: list = []
    for u in units:
        mark = len(taken)
        await consume_layers(db, part=part, location_id=location_id, qty=1, batch_id=u.batch_id, taken=taken)
        if len(taken) > mark:
            u.batch_id = taken[mark]["batch_id"]
    rest = qty - len(units)
    if rest > 1e-9:
        await consume_layers(db, part=part, location_id=location_id, qty=rest, taken=taken)
    ids = list(dict.fromkeys(x["batch_id"] for x in taken))
    cost = {b.id: float(b.unit_cost or 0) for b in (await db.execute(
        select(StockItemBatch).where(StockItemBatch.id.in_(ids)))).scalars()} if ids else {}
    chunks: list = []
    for x in taken:
        c = cost.get(x["batch_id"], 0.0)
        if chunks and chunks[-1][1] == c:
            chunks[-1][0] += x["qty"]
        else:
            chunks.append([x["qty"], c])
    left = qty - sum(q for q, _ in chunks)
    if left > 1e-9:
        chunks.append([left, float(part.avg_cost or part.cost_price or 0)])
    by_batch = {x["batch_id"]: cost.get(x["batch_id"], 0.0) for x in taken}
    for u in units:
        if u.batch_id in by_batch:
            u.unit_cost = Decimal(str(by_batch[u.batch_id]))
    return [(q, c) for q, c in chunks]


async def _shipped(db, t, l, part: Part, *, current) -> list:
    """[(qty, unit_cost)] the line shipped, oldest first: its per-layer transfer_out movements. An
    older transfer has none (its layers were never drawn): draw them from the source now."""
    rows = (await db.execute(select(StockMovement).where(
        StockMovement.ref_type == "transfer", StockMovement.ref_id == t.id,
        StockMovement.part_id == l.part_id, StockMovement.movement_type == "transfer_out",
        StockMovement.created_by_id.is_not(None))
        .order_by(StockMovement.created_at, StockMovement.id))).scalars().all()
    if rows:
        return [(-float(m.qty), float(m.unit_cost or 0)) for m in rows]
    units = await _box_units(db, current.org_id, l.serial_ids, lock=True)
    return await _take_layers(db, part, t.from_location_id, float(l.qty or 0), units)


async def _land_layers(db, org_id: str, part_id: str, location_id: str, warehouse_id, chunks,
                       qty: float, *, refill: bool) -> dict:
    """Put up to `qty` of `chunks` on layers at the location, oldest chunk first; returns
    {unit_cost: layer} so units can point at theirs. `refill`: first top back up a layer there at
    the same cost that has room (a cancel putting units back where they came from)."""
    from backend.models import StockItemBatch
    out: dict = {}
    left = qty
    existing = []
    if refill:
        existing = list((await db.execute(select(StockItemBatch).where(
            StockItemBatch.part_id == part_id, StockItemBatch.location_id == location_id)
            .order_by(StockItemBatch.received_at.desc(), StockItemBatch.id)
            .with_for_update().execution_options(populate_existing=True))).scalars().all())
    warehouse_id = warehouse_id or await stock_rows.warehouse_for(db, location_id)
    stamp = now_utc()
    for i, (q, c) in enumerate(chunks):
        q = min(q, left)
        if q <= 1e-9:
            break
        left -= q
        for b in existing:
            room = float(b.qty_received or 0) - float(b.qty_remaining or 0)
            if q > 1e-9 and room > 1e-9 and abs(float(b.unit_cost or 0) - c) < 0.005:
                back = min(room, q)
                b.qty_remaining = Decimal(str(b.qty_remaining or 0)) + Decimal(str(back))
                q -= back
                out.setdefault(c, b)
        if q > 1e-9:
            b = StockItemBatch(org_id=org_id, part_id=part_id, location_id=location_id,
                               warehouse_id=warehouse_id, unit_cost=Decimal(str(c)),
                               qty_received=Decimal(str(q)), qty_remaining=Decimal(str(q)),
                               received_at=stamp + timedelta(microseconds=i))
            db.add(b)
            out[c] = b
    if out:
        await db.flush()
    return out


def _unit_chunks(units) -> list:
    """[(qty, unit_cost)] for serialized units, one chunk per cost."""
    out: dict = {}
    for u in units:
        c = float(u.unit_cost or 0)
        out[c] = out.get(c, 0) + 1
    return [(q, c) for c, q in out.items()]


def _point_units(units, landed: dict) -> None:
    for u in units:
        c = float(u.unit_cost or 0)
        b = next((v for k, v in landed.items() if abs(k - c) < 0.005), None) or next(iter(landed.values()), None)
        if b is not None:
            u.batch_id = b.id


def _ser(t, loc_names, line_data, by=None):
    return {"id": t.id, "transfer_number": t.transfer_number, "status": t.status,
            "from_location": loc_names.get(t.from_location_id), "to_location": loc_names.get(t.to_location_id),
            "from_location_id": t.from_location_id, "to_location_id": t.to_location_id,
            "note": t.note, "created_at": t.created_at.isoformat() if t.created_at else None,
            "lines": line_data}


@router.get("")
async def list_transfers(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    rows = (await db.execute(select(StockTransfer).where(StockTransfer.org_id == current.org_id)
        .order_by(StockTransfer.created_at.desc()).limit(100))).scalars().all()
    loc_names = {l.id: l.name for l in (await db.execute(
        select(Location).where(Location.org_id == current.org_id))).scalars().all()}
    lines_by = await rows_by(db, StockTransferLine, StockTransferLine.transfer_id, [t.id for t in rows])
    part_names = await names_by_id(db, Part.id, Part.name,
                                   [l.part_id for ls in lines_by.values() for l in ls],
                                   org_col=Part.org_id, org_id=current.org_id)
    out = []
    sns = await _serials_by_line(db, current.org_id, [l for ls in lines_by.values() for l in ls])
    for t in rows:
        ls = lines_by.get(t.id, [])
        out.append(_ser(t, loc_names,
                        [{"id": l.id, "line_id": l.id, "part_id": l.part_id, "part_name": part_names.get(l.part_id),
                          "qty": l.qty, "qty_received": l.qty_received, "serials": sns.get(l.id, [])} for l in ls]))
    return {"items": out}


@router.get("/{tid}")
async def transfer_detail(
    tid: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """One transfer with enriched lines + its lifecycle event feed."""
    from backend.models import EventFeedItem
    t = (await db.execute(select(StockTransfer).where(
        StockTransfer.id == tid, StockTransfer.org_id == current.org_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Transfer not found")
    loc_names = {l.id: l.name for l in (await db.execute(
        select(Location).where(Location.org_id == current.org_id))).scalars().all()}
    ls = await _lines(db, tid)
    parts = {p.id: p for p in (await db.execute(select(Part).where(
        Part.org_id == current.org_id, Part.id.in_({l.part_id for l in ls})))).scalars().all()} if ls else {}
    line_data = []
    total_qty = total_val = 0.0
    sns = await _serials_by_line(db, current.org_id, ls)
    for l in ls:
        p = parts.get(l.part_id)
        cost = (p.avg_cost or p.cost_price or 0) if p else 0
        total_qty += l.qty
        total_val += cost * l.qty
        line_data.append({
            "id": l.id, "line_id": l.id, "qty_received": l.qty_received,   # QA F2: what /receive takes
            "serials": sns.get(l.id, []),
            "part_id": l.part_id, "part_name": p.name if p else l.part_id,
            "sku": p.sku if p else None, "uom": p.uom if p else None,
            "qty": l.qty, "unit_cost": cost, "line_value": q2(cost * l.qty)})
    base = _ser(t, loc_names, line_data)

    events = (await db.execute(select(EventFeedItem).where(
        EventFeedItem.entity_type == "transfer", EventFeedItem.entity_id == t.id)
        .order_by(EventFeedItem.created_at))).scalars().all()
    base["events"] = [{"body": e.body, "author": e.author_name,
                       "at": e.created_at.isoformat() if e.created_at else None} for e in events]
    base["stages"] = ORDERED
    base["stage_index"] = ORDERED.index(t.status) if t.status in ORDERED else (-1 if t.status == "canceled" else 0)
    base["line_count"] = len(line_data)
    base["total_qty"] = total_qty
    base["total_value"] = q2(total_val)
    return base


class TransferLineIn(BaseModel):
    part_id: str
    qty: float = 1
    serials: Optional[list[str]] = Field(None, max_length=500)   # QA F2: a serialized product's units


class TransferIn(BaseModel):
    from_location_id: str
    to_location_id: str
    note: Optional[str] = None
    lines: list[TransferLineIn] = []


@router.post("", status_code=201)
async def create_transfer(
    data: TransferIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    if data.from_location_id == data.to_location_id:
        raise HTTPException(400, "Source and destination must differ")
    if not data.lines:
        raise HTTPException(400, "A transfer needs at least one line")
    # Unknown ids used to fail on the foreign key (500), and another shop's were accepted.
    await require_location(db, current.org_id, data.from_location_id)
    await require_location(db, current.org_id, data.to_location_id)
    await require_owned_ids(db, Part, [li.part_id for li in data.lines], current.org_id, "product", status=404)
    t = StockTransfer(org_id=current.org_id, transfer_number=await next_number(db, current.org_id, "transfer"),
                      from_location_id=data.from_location_id, to_location_id=data.to_location_id,
                      note=data.note, created_by_id=current.id)
    db.add(t)
    await db.flush()
    seen: set = set()
    for li in data.lines:
        if li.qty <= 0:
            raise HTTPException(400, "Quantities must be positive")
        part = (await db.execute(select(Part).where(
            Part.id == li.part_id, Part.org_id == current.org_id))).scalar_one_or_none()
        if not part:
            raise HTTPException(404, "Product not found")
        stock_rows.check_whole(part, li.qty)
        units = await _pick_units(db, current.org_id, part, data.from_location_id, li.serials, li.qty)
        if seen & {u.id for u in units}:
            raise HTTPException(409, "The same unit is on two lines")
        seen |= {u.id for u in units}
        db.add(StockTransferLine(transfer_id=t.id, part_id=li.part_id, qty=li.qty,
                                 serial_ids=[u.id for u in units] or None))
    from backend.routers.feed import log_system_event
    await log_system_event(db, current.org_id, "transfer", t.id,
                           f"Transfer {t.transfer_number} drafted ({len(data.lines)} line(s))")
    await db.commit()
    return {"id": t.id, "transfer_number": t.transfer_number, "status": t.status}


class StatusIn(BaseModel):
    status: str
    return_location_id: Optional[str] = None  # for cancel after shipping


@router.patch("/{tid}/status")
async def change_status(
    tid: str, data: StatusIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    t = (await db.execute(select(StockTransfer).where(
        StockTransfer.id == tid, StockTransfer.org_id == current.org_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Transfer not found")
    if t.status in ("completed", "canceled"):
        raise HTTPException(409, f"Transfer is {t.status} — no further changes")
    new = data.status
    prev = t.status
    lines = await _lines(db, tid)
    if new != "canceled" and (new not in ORDERED or ORDERED.index(new) != ORDERED.index(prev) + 1):
        raise HTTPException(400, f"Invalid move {prev} -> {new}")
    if new == "canceled" and prev in ("in_transit", "received") and not data.return_location_id:
        # goods already left the source — caller names the receiving warehouse
        raise HTTPException(400, "Goods already shipped: pass return_location_id to choose the warehouse taking them back")
    if new == "canceled" and data.return_location_id:
        await require_location(db, current.org_id, data.return_location_id)
    if new == "ready":
        # council M7: holds past their date used to block the box-up until someone opened the
        # reservations list. Released before the claim below, so nothing of this move is committed yet.
        from backend.routers.inventory import _release_expired
        await _release_expired(db, current.org_id)
    # A transfer made "ready" before the count-once fix held its units as reserved AND off the
    # shelf, and logged nothing until it shipped: those still release the hold the old way.
    # (Read before the claim below moves t.status on.)
    legacy_ready = prev == "ready" and not (await db.execute(select(StockMovement.id).where(
        StockMovement.ref_type == "transfer", StockMovement.ref_id == t.id,
        StockMovement.movement_type == "transfer_out").limit(1))).scalar_one_or_none()
    # Claim the move before touching stock: a second identical request (double click, retry)
    # read the same old status, and must not reserve, ship, credit or un-reserve a second time.
    if not await claim_status(db, t, prev, new):
        raise HTTPException(409, "Someone else just changed this transfer. Refresh and try again.")
    # INV-11: the lines as they are now, after the claim — a receive that committed in between set
    # the counts this move credits.
    lines = (await db.execute(select(StockTransferLine).where(StockTransferLine.transfer_id == tid)
                              .execution_options(populate_existing=True))).scalars().all()

    if new == "canceled":
        parts = await _parts(db, current.org_id, lines)
        if prev == "ready":
            # the units were set aside (taken off the shelf) at "ready": put them back
            await _lock_rows(db, lines, t.from_location_id)
            for l in lines:
                await stock_rows.add(db, l.part_id, t.from_location_id, l.qty)
                if legacy_ready:
                    await stock_rows.reserve(db, l.part_id, t.from_location_id, -l.qty)
                else:
                    db.add(StockMovement(part_id=l.part_id, location_id=t.from_location_id,
                                         movement_type="transfer_cancel", qty=l.qty,
                                         ref_type="transfer", ref_id=t.id))
                units = await _box_units(db, current.org_id, l.serial_ids, lock=True)
                for u in units:
                    _place(u, t.from_location_id, t.from_warehouse_id)
                    serial_log.record(db, u, "restored", ref_type="transfer", ref_id=t.id,
                                      label=f"Transfer {t.transfer_number} canceled", actor_id=current.id)
                if not legacy_ready and l.part_id in parts:     # a legacy ready never drew its layers
                    chunks = await _shipped(db, t, l, parts[l.part_id], current=current)
                    landed = await _land_layers(db, current.org_id, l.part_id, t.from_location_id,
                                                t.from_warehouse_id, chunks, float(l.qty or 0), refill=True)
                    _point_units(units, landed)
        elif prev in ("in_transit", "received"):
            back = data.return_location_id
            await _lock_rows(db, lines, t.from_location_id, back)
            for l in lines:
                units = await _box_units(db, current.org_id, l.serial_ids, lock=True)
                for u in units:
                    _place(u, back, None)
                    serial_log.record(db, u, "returned", ref_type="transfer", ref_id=t.id,
                                      label=f"Transfer {t.transfer_number} canceled", actor_id=current.id)
                await stock_rows.add(db, l.part_id, back, l.qty)
                if l.part_id in parts:
                    chunks = await _shipped(db, t, l, parts[l.part_id], current=current)
                    landed = await _land_layers(db, current.org_id, l.part_id, back, None, chunks,
                                                float(l.qty or 0), refill=True)
                    _point_units(units, landed)
                db.add(StockMovement(part_id=l.part_id, location_id=back,
                                     movement_type="receive", qty=l.qty,
                                     ref_type="transfer_cancel", ref_id=t.id))
        from backend.routers.feed import log_system_event
        await log_system_event(db, current.org_id, "transfer", t.id,
                               f"Transfer {t.transfer_number} canceled")
        await db.commit()
        return {"ok": True, "status": t.status}

    if new == "ready":
        # Set the units aside at the source: they come off the shelf now (guarded decrement so two
        # transfers can't grab the same units) and are counted ONCE — not also held as reserved,
        # which used to count them twice (10 on hand, 3 boxed → only 4 left reservable). Units
        # other jobs have reserved can't be boxed.
        await _lock_rows(db, lines, t.from_location_id)
        parts = await _parts(db, current.org_id, lines)
        for l in lines:
            avail, held = await stock_rows.totals(db, l.part_id, t.from_location_id)
            free = avail - max(held, 0)
            if free < l.qty:
                part = (await db.execute(select(Part).where(
                    Part.id == l.part_id, Part.org_id == current.org_id))).scalar_one_or_none()
                raise HTTPException(409, f"Only {max(free, 0):g} of '{part.name if part else l.part_id}' free "
                                         f"at the source warehouse")
            units = await _box_units(db, current.org_id, l.serial_ids, lock=True)
            if any(u.status != "in_stock" or u.location_id != t.from_location_id for u in units):
                gone = next(u for u in units if u.status != "in_stock" or u.location_id != t.from_location_id)
                raise HTTPException(409, f"{gone.serial_number} isn't in stock at the source any more")
            try:      # ii144a: guarded per-bin decrements (a unit from its own bin)
                for u in units:
                    await stock_rows.draw(db, l.part_id, t.from_location_id, 1, bin_id=u.bin_id)
                    u.status = "in_transit"
                    serial_log.record(db, u, "moved", ref_type="transfer", ref_id=t.id,
                                      label=f"Packed for transfer {t.transfer_number}", actor_id=current.id)
                if l.qty - len(units) > 1e-9:
                    await stock_rows.draw(db, l.part_id, t.from_location_id, l.qty - len(units))
            except stock_rows.NotEnoughStock:
                raise HTTPException(409, "Stock changed while reserving — retry")
            # council M3: one movement per layer drawn, at its cost (what "completed" lands)
            chunks = (await _take_layers(db, parts[l.part_id], t.from_location_id, float(l.qty), units)
                      if l.part_id in parts else [(float(l.qty), 0.0)])
            stamp = now_utc()
            for i, (q, c) in enumerate(chunks):
                db.add(StockMovement(part_id=l.part_id, location_id=t.from_location_id,
                                     movement_type="transfer_out", qty=-q, unit_cost=c,
                                     ref_type="transfer", ref_id=t.id, created_by_id=current.id,
                                     created_at=stamp + timedelta(microseconds=i)))
    elif new == "in_transit":
        # leave the source: already off the shelf (and logged) since "ready"
        if legacy_ready:
            for l in lines:
                await stock_rows.reserve(db, l.part_id, t.from_location_id, -l.qty)
                db.add(StockMovement(part_id=l.part_id, location_id=t.from_location_id,
                                     movement_type="transfer_out", qty=-l.qty,
                                     ref_type="transfer", ref_id=t.id))
    elif new == "received":
        t.received_at = now_utc()   # arrived, held for verification
    elif new == "completed":
        # Build 93: credit the destination with what *actually arrived*, not what was
        # shipped. If nobody verified the lines, the shipped quantity stands — but a
        # confirmed shortage must not silently reappear as stock at the far end.
        await _lock_rows(db, lines, t.from_location_id, t.to_location_id)
        parts = await _parts(db, current.org_id, lines)
        for l in lines:
            qty_in = l.qty_received if l.qty_received is not None else l.qty
            if qty_in:
                await stock_rows.add(db, l.part_id, t.to_location_id, qty_in)
            # council M3: what arrived lands on layers at the destination, at the costs it shipped at
            landed = {}
            if l.part_id in parts:
                chunks = await _shipped(db, t, l, parts[l.part_id], current=current)
                if l.serial_ids:     # the units that arrived, each at its own cost, not the oldest ones
                    came = set(l.received_serial_ids if l.received_serial_ids is not None else l.serial_ids)
                    chunks = _unit_chunks([u for u in await _box_units(db, current.org_id, l.serial_ids, lock=True)
                                           if u.id in came])
                landed = await _land_layers(db, current.org_id, l.part_id, t.to_location_id,
                                            t.to_warehouse_id, chunks, float(qty_in or 0), refill=False)
            # QA F2: what shipped comes in, and the shortfall is booked as a loss where it was found.
            # The source already logged the whole shipment out, so a second loss there counted it twice
            # (movements no longer summed to the stock on the shelf).
            if l.qty:
                db.add(StockMovement(part_id=l.part_id, location_id=t.to_location_id,
                                     warehouse_id=t.to_warehouse_id,
                                     movement_type="transfer_in", qty=l.qty,
                                     ref_type="transfer", ref_id=t.id))
            arrived = set(l.received_serial_ids if l.received_serial_ids is not None else (l.serial_ids or []))
            for u in await _box_units(db, current.org_id, l.serial_ids, lock=True):
                if u.id in arrived:
                    _place(u, t.to_location_id, t.to_warehouse_id)
                    _point_units([u], landed)
                    serial_log.record(db, u, "moved", ref_type="transfer", ref_id=t.id,
                                      label=f"Arrived on transfer {t.transfer_number}", actor_id=current.id)
                else:
                    u.status = "written_off"
                    serial_log.record(db, u, "written_off", ref_type="transfer", ref_id=t.id,
                                      label=f"Missing from transfer {t.transfer_number}", actor_id=current.id)
            missing = (l.qty or 0) - qty_in
            if missing > 0:
                db.add(StockMovement(part_id=l.part_id, location_id=t.to_location_id,
                                     warehouse_id=t.to_warehouse_id,
                                     movement_type="transfer_shortage", qty=-missing,
                                     ref_type="transfer", ref_id=t.id))
        t.completed_at = now_utc()

    from backend.routers.feed import log_system_event
    await log_system_event(db, current.org_id, "transfer", t.id,
                           f"Transfer {t.transfer_number}: {new.replace('_', ' ')}")
    await db.commit()
    return {"ok": True, "status": t.status}


# ── Build 93: verify what arrived, and scan lines in ─────────────────────────
class ReceiveLine(BaseModel):
    line_id: Optional[str] = None
    part_id: Optional[str] = None     # QA F2: or name the product, as the person holding the box knows it
    code: Optional[str] = None        # … by its barcode / SKU
    qty_received: float
    serials: Optional[list[str]] = None   # a serialized line: the IMEIs / serials that arrived


class ReceiveIn(BaseModel):
    lines: list[ReceiveLine]


@router.post("/{tid}/receive")
async def receive_lines(
    tid: str, data: ReceiveIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """Confirm, line by line, what actually turned up at the destination.

    This is the step that catches a box that left with ten and arrived with nine. Until
    it's done, completing the transfer assumes everything arrived; once it's done, the
    destination is credited with the verified count and the difference is booked as a
    loss on arrival rather than quietly vanishing.
    """
    # Audit INV-11: locked and read fresh — a complete (or cancel) that claimed the transfer first
    # is seen here, and one that comes after waits for this receive (its claim updates this row).
    t = (await db.execute(select(StockTransfer).where(
        StockTransfer.id == tid, StockTransfer.org_id == current.org_id)
        .with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Transfer not found")
    if t.status not in ("in_transit", "received"):
        raise HTTPException(409, f"Can't verify a transfer that is {t.status}")

    discrepancies = 0
    for r in data.lines:
        ln = await _receive_line(db, current.org_id, tid, r)
        if r.qty_received < 0:
            raise HTTPException(422, "Received quantity can't be negative")
        part = (await db.execute(select(Part).where(Part.id == ln.part_id))).scalar_one_or_none()
        if part:
            stock_rows.check_whole(part, r.qty_received)
        if r.qty_received > (ln.qty or 0):
            raise HTTPException(
                409, f"Received {r.qty_received:g} but only {ln.qty:g} was shipped — "
                     f"check the line before confirming.")
        ln.qty_received = r.qty_received
        if ln.serial_ids:
            ln.received_serial_ids = await _arrived_units(db, current.org_id, ln, r)
        if r.qty_received != ln.qty:
            discrepancies += 1

    if t.status == "in_transit":
        t.status = "received"
        t.received_at = now_utc()
    await db.commit()
    return {"ok": True, "status": t.status, "discrepancies": discrepancies}


async def _receive_line(db, org_id: str, tid: str, r: ReceiveLine) -> StockTransferLine:
    """The transfer line a received row means: its line id, else the one line for the product."""
    q = (select(StockTransferLine).where(StockTransferLine.transfer_id == tid)
         .with_for_update().execution_options(populate_existing=True))
    if r.line_id:
        ln = (await db.execute(q.where(StockTransferLine.id == r.line_id))).scalar_one_or_none()
        if not ln:
            raise HTTPException(404, f"Line {r.line_id} not found on this transfer")
        return ln
    part_id = r.part_id
    if not part_id and r.code:
        from backend.services import product_codes
        part_id = (await db.execute(select(Part.id).where(
            Part.org_id == org_id, product_codes.matches_code(org_id, r.code.strip())))).scalars().first()
    if not part_id:
        raise HTTPException(422, "Say which line arrived: line_id, part_id or code")
    found = (await db.execute(q.where(StockTransferLine.part_id == part_id))).scalars().all()
    if not found:
        raise HTTPException(404, "That product isn't on this transfer")
    if len(found) > 1:
        raise HTTPException(409, "That product is on more than one line — send the line_id")
    return found[0]


async def _arrived_units(db, org_id: str, ln: StockTransferLine, r: ReceiveLine) -> list:
    """Which of a serialized line's units arrived: the serials named, or all of them when none is
    short. A short serialized line has to say which phones are missing."""
    from backend.services.serial_intake import normalize as _sn_norm
    units = await _box_units(db, org_id, ln.serial_ids)
    if not r.serials:
        if abs(r.qty_received - len(units)) > 1e-9:
            raise HTTPException(422, "Scan the IMEI / serial of each unit that arrived")
        return [u.id for u in units]
    key = lambda v: _sn_norm(v)[0] or v.strip()   # noqa: E731
    by_sn = {key(u.serial_number): u.id for u in units}
    got = list(dict.fromkeys(by_sn.get(key(x)) for x in r.serials if x and x.strip()))
    if None in got:
        raise HTTPException(409, "A serial that arrived isn't on this transfer")
    if len(got) != r.qty_received:
        raise HTTPException(422, f"{len(got)} serials for {r.qty_received:g} received")
    return got


class TransferScanIn(BaseModel):
    code: str                 # barcode or SKU
    qty: float = 1


@router.post("/{tid}/scan")
async def scan_transfer_line(
    tid: str, data: TransferScanIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """Build lines by scanning items into the box. Repeat scans add up rather than
    replacing, which is what pointing a scanner at a shelf actually means."""
    t = (await db.execute(select(StockTransfer).where(
        StockTransfer.id == tid, StockTransfer.org_id == current.org_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Transfer not found")
    if t.status != "draft":
        raise HTTPException(409, "Lines can only be added while the transfer is a draft")

    if data.qty <= 0:
        raise HTTPException(422, "Scan a quantity above zero — remove a line by editing the transfer")
    code = data.code.strip()
    from backend.services import product_codes
    part = (await db.execute(select(Part).where(
        Part.org_id == current.org_id, product_codes.matches_code(current.org_id, code)))).scalars().first()
    unit = None
    if not part:      # QA F2: a phone's IMEI says which unit goes in the box
        from backend.services.serial_intake import matches as _sn_matches
        unit = (await db.execute(select(SerializedItem).where(
            SerializedItem.org_id == current.org_id, SerializedItem.status == "in_stock",
            SerializedItem.location_id == t.from_location_id,
            _sn_matches(SerializedItem.serial_number, code)))).scalars().first()
        if unit:
            part = (await db.execute(select(Part).where(
                Part.id == unit.part_id, Part.org_id == current.org_id))).scalar_one_or_none()
    if not part:
        raise HTTPException(404, f"Nothing matches “{code}”")
    if part.serialized and not unit:
        raise HTTPException(422, f"Scan the IMEI / serial of the “{part.name}” going in the box")
    qty = 1 if unit else data.qty
    stock_rows.check_whole(part, qty)

    ln = (await db.execute(select(StockTransferLine).where(
        StockTransferLine.transfer_id == tid,
        StockTransferLine.part_id == part.id))).scalars().first()
    if ln:
        await add_to_column(db, ln, "qty", qty, round_to=None)   # two scanners on one box both count
        if unit:      # the row is locked by the increment: the unit list can't lose a concurrent scan
            await db.refresh(ln, attribute_names=["serial_ids"])
            if unit.id in (ln.serial_ids or []):
                raise HTTPException(409, f"{unit.serial_number} is already in the box")
            ln.serial_ids = [*(ln.serial_ids or []), unit.id]
    else:
        ln = StockTransferLine(transfer_id=tid, part_id=part.id, qty=qty,
                               serial_ids=[unit.id] if unit else None)
        db.add(ln)
    await db.commit()
    await db.refresh(ln)
    serials = (await _serials_by_line(db, current.org_id, [ln])).get(ln.id, [])
    return {"ok": True, "line_id": ln.id, "part_id": part.id, "part_name": part.name, "qty": ln.qty,
            "serials": serials}
