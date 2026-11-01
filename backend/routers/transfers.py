"""Inter-branch transfers with transit statuses (Orderry Mar-2026 model).

State machine and stock semantics:
  draft       editable, no stock effect
  ready       source qty_available -= qty, qty_reserved += qty  (reserved, still at source)
  in_transit  source qty_reserved -= qty; goods now ride ON the transfer (virtual in-transit)
  received    arrived at destination, still held for verification (no change)
  completed   destination qty_available += qty (goods land)
  canceled    before shipping: reservation returns to source availability;
              after shipping: caller names the warehouse that takes goods back.
Every hop writes StockMovements and a feed event. Mutations require
can_manage_inventory."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import (
    Employee,
    Location,
    Part,
    StockMovement,
    StockTransfer,
    StockTransferLine,
)
from backend.services.auth import get_current_employee, require_perm
from backend.services import stock_rows
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
    for t in rows:
        ls = lines_by.get(t.id, [])
        out.append(_ser(t, loc_names,
                        [{"id": l.id, "line_id": l.id, "part_id": l.part_id, "part_name": part_names.get(l.part_id),
                          "qty": l.qty, "qty_received": l.qty_received} for l in ls]))
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
    for l in ls:
        p = parts.get(l.part_id)
        cost = (p.avg_cost or p.cost_price or 0) if p else 0
        total_qty += l.qty
        total_val += cost * l.qty
        line_data.append({
            "id": l.id, "line_id": l.id, "qty_received": l.qty_received,   # QA F2: what /receive takes
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
    for li in data.lines:
        if li.qty <= 0:
            raise HTTPException(400, "Quantities must be positive")
        db.add(StockTransferLine(transfer_id=t.id, part_id=li.part_id, qty=li.qty))
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
    # Claim the move before touching stock: a second identical request (double click, retry)
    # read the same old status, and must not reserve, ship, credit or un-reserve a second time.
    if not await claim_status(db, t, prev, new):
        raise HTTPException(409, "Someone else just changed this transfer. Refresh and try again.")

    if new == "canceled":
        if prev == "ready":
            # un-reserve back to availability at source
            for l in lines:
                await stock_rows.reserve(db, l.part_id, t.from_location_id, -l.qty)
                await stock_rows.add(db, l.part_id, t.from_location_id, l.qty)
        elif prev in ("in_transit", "received"):
            back = data.return_location_id
            for l in lines:
                await stock_rows.add(db, l.part_id, back, l.qty)
                db.add(StockMovement(part_id=l.part_id, location_id=back,
                                     movement_type="receive", qty=l.qty,
                                     ref_type="transfer_cancel", ref_id=t.id))
        from backend.routers.feed import log_system_event
        await log_system_event(db, current.org_id, "transfer", t.id,
                               f"Transfer {t.transfer_number} canceled")
        await db.commit()
        return {"ok": True, "status": t.status}

    if new == "ready":
        # reserve at source: guarded decrement so two transfers can't grab the same units
        for l in lines:
            avail, _held = await stock_rows.totals(db, l.part_id, t.from_location_id)
            if avail < l.qty:
                part = (await db.execute(select(Part).where(Part.id == l.part_id))).scalar_one_or_none()
                raise HTTPException(409, f"Only {avail:g} of '{part.name if part else l.part_id}' at the source warehouse")
            try:      # ii144a: guarded per-bin decrements, then held on the location
                await stock_rows.draw(db, l.part_id, t.from_location_id, l.qty)
            except stock_rows.NotEnoughStock:
                raise HTTPException(409, "Stock changed while reserving — retry")
            await stock_rows.reserve(db, l.part_id, t.from_location_id, l.qty)
    elif new == "in_transit":
        # leave the source: reservation lifts; goods now ride on the transfer itself
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
        for l in lines:
            qty_in = l.qty_received if l.qty_received is not None else l.qty
            if qty_in:
                await stock_rows.add(db, l.part_id, t.to_location_id, qty_in)
            # QA F2: what shipped comes in, and the shortfall is booked as a loss where it was found.
            # The source already logged the whole shipment out, so a second loss there counted it twice
            # (movements no longer summed to the stock on the shelf).
            if l.qty:
                db.add(StockMovement(part_id=l.part_id, location_id=t.to_location_id,
                                     warehouse_id=t.to_warehouse_id,
                                     movement_type="transfer_in", qty=l.qty,
                                     ref_type="transfer", ref_id=t.id))
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
    t = (await db.execute(select(StockTransfer).where(
        StockTransfer.id == tid, StockTransfer.org_id == current.org_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Transfer not found")
    if t.status not in ("in_transit", "received"):
        raise HTTPException(409, f"Can't verify a transfer that is {t.status}")

    discrepancies = 0
    for r in data.lines:
        ln = await _receive_line(db, current.org_id, tid, r)
        if r.qty_received < 0:
            raise HTTPException(422, "Received quantity can't be negative")
        if r.qty_received > (ln.qty or 0):
            raise HTTPException(
                409, f"Received {r.qty_received:g} but only {ln.qty:g} was shipped — "
                     f"check the line before confirming.")
        ln.qty_received = r.qty_received
        if r.qty_received != ln.qty:
            discrepancies += 1

    if t.status == "in_transit":
        t.status = "received"
        t.received_at = now_utc()
    await db.commit()
    return {"ok": True, "status": t.status, "discrepancies": discrepancies}


async def _receive_line(db, org_id: str, tid: str, r: ReceiveLine) -> StockTransferLine:
    """The transfer line a received row means: its line id, else the one line for the product."""
    q = select(StockTransferLine).where(StockTransferLine.transfer_id == tid)
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
    if not part:
        raise HTTPException(404, f"Nothing matches “{code}”")

    ln = (await db.execute(select(StockTransferLine).where(
        StockTransferLine.transfer_id == tid,
        StockTransferLine.part_id == part.id))).scalar_one_or_none()
    if ln:
        await add_to_column(db, ln, "qty", data.qty, round_to=None)   # two scanners on one box both count
    else:
        ln = StockTransferLine(transfer_id=tid, part_id=part.id, qty=data.qty)
        db.add(ln)
    await db.commit()
    await db.refresh(ln)
    return {"ok": True, "line_id": ln.id, "part_id": part.id, "part_name": part.name, "qty": ln.qty}
