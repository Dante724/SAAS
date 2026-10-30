"""Builds 87/88 — supplier returns and write-off documents.

Both were previously fire-and-forget stock movements, which meant a mistake could
never be corrected — you had to post a compensating adjustment and the history lied.
Orderry models each as a **document**:

  * **Return to supplier** — the mirror of a posting. Stock leaves; the money is
    credited back to the supplier's balance or refunded to a cashbox. A return whose
    payment has already been made can't be deleted until that payment is removed.
  * **Write-off** — deleting the document **restores the stock**. Lines carry comments
    that are internal by default but can be made visible to the client.
"""
from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import (
    Employee,
    Part,
    Posting,
    PostingLine,
    PurchaseReturn,
    PurchaseReturnLine,
    SerializedItem,
    StockLevel,
    StockMovement,
    Supplier,
    WriteOff,
    WriteOffLine,
)
from backend.services import warehouses as wh
from backend.services.auth import get_current_employee, require_perm
from backend.services.money import q2, q4

router = APIRouter(prefix="/api/stock-docs", tags=["inventory"])


class DocLine(BaseModel):
    part_id: str
    qty: float = 1
    unit_cost: Optional[float] = None

    @field_validator("qty")
    @classmethod
    def _qty_positive(cls, v):
        # a negative qty passed _take_stock's "have < qty" check and ADDED stock
        if v <= 0:
            raise ValueError("Quantity must be greater than zero")
        return v

    @field_validator("unit_cost")
    @classmethod
    def _cost_non_negative(cls, v):
        if v is not None and v < 0:
            raise ValueError("Cost can't be negative")
        return v
    serialized_item_id: Optional[str] = None
    bin_id: Optional[str] = None
    comment: Optional[str] = None
    comment_public: bool = False


async def _stock_row(db: AsyncSession, part_id: str, warehouse) -> Optional[StockLevel]:
    # Prefer a warehouse-keyed row; fall back to the location-level row that postings, sales
    # and PO receipts actually write (they key StockLevel by location_id, warehouse_id NULL).
    # 144bg: without this, write-offs/transfers read an empty warehouse-only row set and 409.
    row = (await db.execute(select(StockLevel).where(
        StockLevel.part_id == part_id,
        StockLevel.warehouse_id == warehouse.id))).scalar_one_or_none()
    if row is None:
        row = (await db.execute(select(StockLevel).where(
            StockLevel.part_id == part_id,
            StockLevel.location_id == warehouse.location_id))).scalar_one_or_none()
    return row


async def _take_stock(db: AsyncSession, part_id: str, warehouse, qty: float, *,
                      doc: str) -> None:
    sl = await _stock_row(db, part_id, warehouse)
    have = (sl.qty_available if sl else 0) or 0
    if have < qty:
        name = (await db.execute(select(Part.name).where(Part.id == part_id))).scalar_one_or_none()
        raise HTTPException(
            409, f"Only {have:g} of “{name}” in this warehouse — can't {doc} {qty:g}.")
    await _atomic_add(db, sl, "qty_available", -(qty), round_to=None)   # atomic
    await _deplete_batches_fefo(db, part_id, warehouse, qty)


async def _deplete_batches_fefo(db: AsyncSession, part_id: str, warehouse, qty: float) -> None:
    """First-Expired-First-Out batch depletion.

    Aggregate stock only knows *how much* is on the shelf; batches know *which* stock and
    *when it expires*. When we remove stock, the batches with the nearest expiry must go
    first — otherwise a write-off would leave the oldest goods sitting on the shelf and
    the expiration report would keep flagging stock that, on paper, we'd already removed.

    Parts without any tracked batches are a no-op: the aggregate decrement in the caller
    is all there is for them, so non-perishable inventory is unaffected.
    """
    from backend.models import StockItemBatch

    batches = (await db.execute(select(StockItemBatch).where(
        StockItemBatch.part_id == part_id,
        StockItemBatch.warehouse_id == warehouse.id,
        StockItemBatch.qty_remaining > 0)
        # NULL expiry sorts last: dated (perishable) stock is consumed before undated
        .order_by(StockItemBatch.expiry_date.is_(None), StockItemBatch.expiry_date,
                  StockItemBatch.received_at))).scalars().all()

    remaining = float(qty)
    for b in batches:
        if remaining <= 0:
            break
        take = min(float(b.qty_remaining or 0), remaining)
        b.qty_remaining = float(b.qty_remaining or 0) - take
        remaining -= take
    # If batches don't cover the full quantity (untracked receipts predate batch tracking),
    # the aggregate decrement already handled it — we simply drain what batches we have.


async def _give_stock(db: AsyncSession, part_id: str, warehouse, qty: float,
                      location_id: Optional[str]) -> None:
    sl = await _stock_row(db, part_id, warehouse)
    if sl:
        await _atomic_add(db, sl, "qty_available", qty, round_to=None)   # atomic
    else:
        db.add(StockLevel(part_id=part_id, location_id=location_id,
                          warehouse_id=warehouse.id, qty_available=qty, qty_reserved=0))


async def _cost(db: AsyncSession, part_id: str, given: Optional[float]) -> Decimal:
    # An explicit unit_cost is the credit agreed with the supplier (routes require
    # can_manage_inventory); DocLine rejects negative values.
    if given is not None:
        return Decimal(str(given))
    p = (await db.execute(select(Part).where(Part.id == part_id))).scalar_one_or_none()
    return Decimal(str((p.avg_cost or p.cost_price or 0) if p else 0))


# ══ Build 87: returns to supplier ════════════════════════════════════════════
class ReturnIn(BaseModel):
    supplier_id: Optional[str] = None
    warehouse_id: Optional[str] = None
    location_id: Optional[str] = None      # compatibility shim
    settlement: str = "balance"            # balance|cash
    account_id: Optional[str] = None
    note: Optional[str] = None
    lines: list[DocLine]


def _ser_return(r: PurchaseReturn, lines: list[PurchaseReturnLine]) -> dict:
    return {"id": r.id, "number": r.number, "warehouse_id": r.warehouse_id,
            "supplier_id": r.supplier_id, "settlement": r.settlement,
            "account_id": r.account_id, "total": float(r.total or 0),
            "status": r.status, "paid": r.paid, "note": r.note,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "lines": [{"id": ln.id, "part_id": ln.part_id, "qty": ln.qty,
                       "unit_cost": float(ln.unit_cost or 0),
                       "total": float(ln.total or 0),
                       "serialized_item_id": ln.serialized_item_id} for ln in lines]}


@router.get("/returns")
async def list_returns(
    supplier_id: Optional[str] = None,
    warehouse_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    q = select(PurchaseReturn).where(PurchaseReturn.org_id == current.org_id,
                                     PurchaseReturn.status != "deleted")
    if supplier_id:
        q = q.where(PurchaseReturn.supplier_id == supplier_id)
    if warehouse_id:
        q = q.where(PurchaseReturn.warehouse_id == warehouse_id)
    rows = (await db.execute(q.order_by(PurchaseReturn.created_at.desc()).limit(200))).scalars().all()
    out = []
    for r in rows:
        lines = (await db.execute(select(PurchaseReturnLine).where(
            PurchaseReturnLine.return_id == r.id))).scalars().all()
        out.append(_ser_return(r, lines))
    return {"items": out}


@router.post("/returns", status_code=201)
async def create_return(
    data: ReturnIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    if not data.lines:
        raise HTTPException(400, "A return needs at least one line")
    if data.settlement not in ("balance", "cash"):
        raise HTTPException(422, "settlement must be balance|cash")
    w = await wh.guard_action(db, current, "writeoff",
                              warehouse_id=data.warehouse_id, location_id=data.location_id)
    if not w:
        raise HTTPException(400, "No warehouse resolved — pass warehouse_id")
    if data.supplier_id:
        sup = (await db.execute(select(Supplier).where(
            Supplier.id == data.supplier_id,
            Supplier.org_id == current.org_id))).scalar_one_or_none()
        if not sup:
            raise HTTPException(404, "Supplier not found")

    from backend.services.numbering import next_number
    try:
        number = await next_number(db, current.org_id, "purchase_return")
    except Exception:
        number = None

    doc = PurchaseReturn(
        org_id=current.org_id, number=number, warehouse_id=w.id,
        supplier_id=data.supplier_id, settlement=data.settlement,
        account_id=data.account_id, note=data.note, created_by_id=current.id)
    db.add(doc)
    await db.flush()

    total = Decimal("0")
    for ln in data.lines:
        cost = await _cost(db, ln.part_id, ln.unit_cost)
        await _take_stock(db, ln.part_id, w, ln.qty, doc="return")
        if ln.serialized_item_id:
            unit = (await db.execute(select(SerializedItem).where(
                SerializedItem.id == ln.serialized_item_id, SerializedItem.org_id == current.org_id,
                SerializedItem.part_id == ln.part_id))).scalar_one_or_none()
            if unit:
                unit.status = "written_off"
        line_total = cost * Decimal(str(ln.qty))
        total += line_total
        db.add(PurchaseReturnLine(
            return_id=doc.id, part_id=ln.part_id, serialized_item_id=ln.serialized_item_id,
            qty=ln.qty, unit_cost=cost, total=line_total))
        db.add(StockMovement(
            part_id=ln.part_id, location_id=w.location_id, warehouse_id=w.id,
            movement_type="return_to_supplier", qty=-ln.qty, created_by_id=current.id))

    doc.total = total
    # settlement: credit the supplier's balance, or take cash back from an account
    if data.settlement == "balance" and data.supplier_id:
        sup = (await db.execute(select(Supplier).where(
            Supplier.id == data.supplier_id))).scalar_one()
        # 144ax: single write-path — a return means we owe them less (−total). Column + ledger
        # move together through one helper so they can never drift.
        from backend.services.ledger import adjust_supplier_ap
        await adjust_supplier_ap(db, current.org_id, sup, -float(total), entry_type="purchase_return",
                                 note=f"Return {doc.number or ''}".strip(),
                                 ref_type="purchase_return", ref_id=doc.id, created_by_id=current.id)
    elif data.settlement == "cash" and data.account_id:
        # 144av: supplier refunds us in cash → money lands in the chosen cashbox. Previously the
        # account_id was accepted and silently dropped, so the refund never hit the books.
        from backend.routers.cash import post_transaction
        await post_transaction(db, current.org_id, data.account_id, "income", float(total),
                               comment=f"Cash refund for return {doc.number or ''}".strip(),
                               ref_type="purchase_return", ref_id=doc.id, employee_id=current.id)
        doc.paid = True
    await db.commit()
    await db.refresh(doc)
    lines = (await db.execute(select(PurchaseReturnLine).where(
        PurchaseReturnLine.return_id == doc.id))).scalars().all()
    return _ser_return(doc, lines)


@router.delete("/returns/{return_id}")
async def delete_return(
    return_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """Deleting a return puts the stock back. If the supplier has already paid the
    refund out, the payment has to go first — otherwise the books would drift."""
    doc = (await db.execute(select(PurchaseReturn).where(
        PurchaseReturn.id == return_id,
        PurchaseReturn.org_id == current.org_id))).scalar_one_or_none()
    if not doc or doc.status == "deleted":
        raise HTTPException(404, "Return not found")
    if doc.paid:
        raise HTTPException(
            409, "This return has been paid. Delete the payment first, then the return.")

    w = await wh.resolve(db, current.org_id, warehouse_id=doc.warehouse_id)
    lines = (await db.execute(select(PurchaseReturnLine).where(
        PurchaseReturnLine.return_id == doc.id))).scalars().all()
    for ln in lines:
        await _give_stock(db, ln.part_id, w, ln.qty, w.location_id if w else None)
        if ln.serialized_item_id:
            unit = (await db.execute(select(SerializedItem).where(
                SerializedItem.id == ln.serialized_item_id, SerializedItem.org_id == current.org_id,
                SerializedItem.part_id == ln.part_id))).scalar_one_or_none()
            if unit:
                unit.status = "in_stock"
        db.add(StockMovement(
            part_id=ln.part_id, location_id=w.location_id if w else None,
            warehouse_id=doc.warehouse_id, movement_type="return_reverted",
            qty=ln.qty, created_by_id=current.id))
    doc.status = "deleted"
    # 144ax: only a BALANCE-settled return ever moved the AP balance on create, so only that
    # reverses it here. (Previously the column was bumped unconditionally — a cash-settled return,
    # which is paid from the cashbox, had its deletion wrongly inflate the AP balance with no
    # matching ledger row → drift.) Single write-path keeps column + ledger in lockstep.
    if doc.supplier_id and doc.settlement == "balance":
        sup = (await db.execute(select(Supplier).where(
            Supplier.id == doc.supplier_id))).scalar_one_or_none()
        if sup is not None:
            from backend.services.ledger import adjust_supplier_ap
            await adjust_supplier_ap(db, current.org_id, sup, float(doc.total or 0), entry_type="purchase_return",
                                     note=f"Return {doc.number or ''} deleted".strip(),
                                     ref_type="purchase_return", ref_id=doc.id, created_by_id=current.id)
    await db.commit()
    return {"ok": True, "stock_restored": True}


# ══ Build 88: write-off documents ════════════════════════════════════════════
class WriteOffIn(BaseModel):
    warehouse_id: Optional[str] = None
    location_id: Optional[str] = None
    reason: Optional[str] = None
    note: Optional[str] = None
    lines: list[DocLine]


def _ser_wo(d: WriteOff, lines: list[WriteOffLine]) -> dict:
    return {"id": d.id, "number": d.number, "warehouse_id": d.warehouse_id,
            "reason": d.reason, "status": d.status, "note": d.note,
            "total_cost": float(d.total_cost or 0),
            "created_at": d.created_at.isoformat() if d.created_at else None,
            "reverted_at": d.reverted_at.isoformat() if d.reverted_at else None,
            "lines": [{"id": ln.id, "part_id": ln.part_id, "qty": ln.qty,
                       "unit_cost": float(ln.unit_cost or 0), "bin_id": ln.bin_id,
                       "comment": ln.comment, "comment_public": ln.comment_public,
                       "serialized_item_id": ln.serialized_item_id} for ln in lines]}


@router.get("/write-offs")
async def list_write_offs(
    warehouse_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    q = select(WriteOff).where(WriteOff.org_id == current.org_id)
    if warehouse_id:
        q = q.where(WriteOff.warehouse_id == warehouse_id)
    rows = (await db.execute(q.order_by(WriteOff.created_at.desc()).limit(200))).scalars().all()
    # 144bg — resolve part names once so the list view can show a readable summary row.
    from backend.models import Part
    pname = dict((await db.execute(select(Part.id, Part.name).where(
        Part.org_id == current.org_id))).all())
    out = []
    for d in rows:
        lines = (await db.execute(select(WriteOffLine).where(
            WriteOffLine.write_off_id == d.id))).scalars().all()
        row = _ser_wo(d, lines)
        # Summary fields the audit table binds to (part_name, qty).
        row["qty"] = sum((ln.qty or 0) for ln in lines)
        if lines:
            first = pname.get(lines[0].part_id, "Item")
            row["part_name"] = first if len(lines) == 1 else f"{first} +{len(lines) - 1} more"
        else:
            row["part_name"] = "—"
        out.append(row)
    return {"items": out}


@router.post("/write-offs", status_code=201)
async def create_write_off(
    data: WriteOffIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    if not data.lines:
        raise HTTPException(400, "A write-off needs at least one line")
    w = await wh.guard_action(db, current, "writeoff",
                              warehouse_id=data.warehouse_id, location_id=data.location_id)
    if not w:
        raise HTTPException(400, "No warehouse resolved — pass warehouse_id")

    from backend.services.numbering import next_number
    try:
        number = await next_number(db, current.org_id, "write_off")
    except Exception:
        number = None

    doc = WriteOff(org_id=current.org_id, number=number, warehouse_id=w.id,
                   reason=data.reason, note=data.note, created_by_id=current.id)
    db.add(doc)
    await db.flush()

    total = Decimal("0")
    for ln in data.lines:
        cost = await _cost(db, ln.part_id, ln.unit_cost)
        await _take_stock(db, ln.part_id, w, ln.qty, doc="write off")
        if ln.serialized_item_id:
            unit = (await db.execute(select(SerializedItem).where(
                SerializedItem.id == ln.serialized_item_id, SerializedItem.org_id == current.org_id,
                SerializedItem.part_id == ln.part_id))).scalar_one_or_none()
            if unit:
                unit.status = "written_off"
        total += cost * Decimal(str(ln.qty))
        db.add(WriteOffLine(
            write_off_id=doc.id, part_id=ln.part_id, qty=ln.qty, unit_cost=cost,
            serialized_item_id=ln.serialized_item_id, bin_id=ln.bin_id,
            comment=ln.comment, comment_public=ln.comment_public))
        db.add(StockMovement(
            part_id=ln.part_id, location_id=w.location_id, warehouse_id=w.id,
            movement_type="writeoff", qty=-ln.qty, created_by_id=current.id))

    doc.total_cost = total
    await db.commit()
    await db.refresh(doc)
    lines = (await db.execute(select(WriteOffLine).where(
        WriteOffLine.write_off_id == doc.id))).scalars().all()
    return _ser_wo(doc, lines)


class WriteOffLinePatch(BaseModel):
    qty: Optional[float] = None

    @field_validator("qty")
    @classmethod
    def _qty_positive(cls, v):
        if v is not None and v <= 0:
            raise ValueError("Quantity must be greater than zero")
        return v
    comment: Optional[str] = None
    comment_public: Optional[bool] = None


@router.patch("/write-offs/{doc_id}/lines/{line_id}")
async def edit_write_off_line(
    doc_id: str, line_id: str, data: WriteOffLinePatch,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """Correct a write-off after the fact. Changing the quantity settles the difference
    against stock, so the books stay true rather than needing a compensating entry."""
    doc = (await db.execute(select(WriteOff).where(
        WriteOff.id == doc_id, WriteOff.org_id == current.org_id))).scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Write-off not found")
    if doc.status == "reverted":
        raise HTTPException(409, "This write-off has been reverted")
    ln = (await db.execute(select(WriteOffLine).where(
        WriteOffLine.id == line_id, WriteOffLine.write_off_id == doc_id))).scalar_one_or_none()
    if not ln:
        raise HTTPException(404, "Line not found")

    w = await wh.resolve(db, current.org_id, warehouse_id=doc.warehouse_id)
    if data.qty is not None and data.qty != ln.qty:
        if data.qty <= 0:
            raise HTTPException(400, "Quantity must be positive — delete the document instead")
        delta = data.qty - ln.qty
        if delta > 0:
            await _take_stock(db, ln.part_id, w, delta, doc="write off")
        else:
            await _give_stock(db, ln.part_id, w, -delta, w.location_id if w else None)
        db.add(StockMovement(
            part_id=ln.part_id, location_id=w.location_id if w else None,
            warehouse_id=doc.warehouse_id, movement_type="writeoff_adjust",
            qty=-delta, created_by_id=current.id))
        ln.qty = data.qty
    if data.comment is not None:
        ln.comment = data.comment
    if data.comment_public is not None:
        ln.comment_public = data.comment_public

    lines = (await db.execute(select(WriteOffLine).where(
        WriteOffLine.write_off_id == doc.id))).scalars().all()
    doc.total_cost = sum((Decimal(str(x.unit_cost or 0)) * Decimal(str(x.qty))
                          for x in lines), Decimal("0"))
    await db.commit()
    return _ser_wo(doc, lines)


@router.delete("/write-offs/{doc_id}")
async def delete_write_off(
    doc_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """Deleting a write-off **restores the stock** — that's the whole point of making
    it a document. The document is kept as reverted rather than erased."""
    doc = (await db.execute(select(WriteOff).where(
        WriteOff.id == doc_id, WriteOff.org_id == current.org_id))).scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Write-off not found")
    if doc.status == "reverted":
        return {"ok": True, "already_reverted": True}

    w = await wh.resolve(db, current.org_id, warehouse_id=doc.warehouse_id)
    lines = (await db.execute(select(WriteOffLine).where(
        WriteOffLine.write_off_id == doc.id))).scalars().all()
    for ln in lines:
        await _give_stock(db, ln.part_id, w, ln.qty, w.location_id if w else None)
        if ln.serialized_item_id:
            unit = (await db.execute(select(SerializedItem).where(
                SerializedItem.id == ln.serialized_item_id, SerializedItem.org_id == current.org_id,
                SerializedItem.part_id == ln.part_id))).scalar_one_or_none()
            if unit:
                unit.status = "in_stock"
        db.add(StockMovement(
            part_id=ln.part_id, location_id=w.location_id if w else None,
            warehouse_id=doc.warehouse_id, movement_type="writeoff_reverted",
            qty=ln.qty, created_by_id=current.id))
    doc.status = "reverted"
    doc.reverted_at = datetime.utcnow()
    await db.commit()
    return {"ok": True, "stock_restored": True}


# ══ Build 93: postings as documents ══════════════════════════════════════════
class PostLine(BaseModel):
    part_id: str
    qty: float
    unit_cost: float = 0.0

    @field_validator("qty")
    @classmethod
    def _qty_positive(cls, v):
        if v <= 0:
            raise ValueError("Quantity must be greater than zero")
        return v

    @field_validator("unit_cost")
    @classmethod
    def _cost_non_negative(cls, v):
        if v < 0:
            raise ValueError("Cost can't be negative")
        return v
    markup_pct: Optional[float] = None     # sets the sell price at posting time
    price_type_id: Optional[str] = None    # which price list the markup writes to


class PostingIn(BaseModel):
    warehouse_id: Optional[str] = None
    location_id: Optional[str] = None      # compatibility shim
    supplier_id: Optional[str] = None
    settlement: str = "balance"            # balance|cash
    account_id: Optional[str] = None
    reference: Optional[str] = None        # supplier invoice number
    note: Optional[str] = None
    lines: list[PostLine]


def _ser_posting(d: Posting, lines: list[PostingLine]) -> dict:
    return {"id": d.id, "number": d.number, "warehouse_id": d.warehouse_id,
            "supplier_id": d.supplier_id, "settlement": d.settlement,
            "account_id": d.account_id, "reference": d.reference,
            "total": float(d.total or 0), "status": d.status, "paid": d.paid,
            "note": d.note,
            "created_at": d.created_at.isoformat() if d.created_at else None,
            "lines": [{"id": ln.id, "part_id": ln.part_id, "qty": ln.qty,
                       "unit_cost": float(ln.unit_cost or 0),
                       "markup_pct": ln.markup_pct, "price_type_id": ln.price_type_id,
                       "total": float(ln.total or 0)} for ln in lines]}


async def _apply_posting_line(db: AsyncSession, current: Employee, w, ln, *,
                              sign: int) -> Decimal:
    """Move stock and keep the moving-average cost honest.

    Averaging only makes sense on the way *in*. On a reversal we put the quantity back
    but deliberately leave avg_cost alone: un-averaging isn't a well-defined operation,
    and pretending otherwise would quietly corrupt the cost basis of everything else.
    """
    part = (await db.execute(select(Part).where(
        Part.id == ln.part_id, Part.org_id == current.org_id))).scalar_one_or_none()
    if not part:
        raise HTTPException(404, f"Part {ln.part_id} not found")

    if sign > 0:
        cost = Decimal(str(ln.unit_cost or 0))
        # Weight against GLOBAL on-hand at 4dp, exactly like stock_posting.apply_receipt_line:
        # avg_cost is one per-part column, so averaging against a single warehouse's quantity
        # (and rounding to 2dp) gave a different cost basis depending on which screen posted it.
        from sqlalchemy import func as _func
        prev_qty = float((await db.execute(select(_func.coalesce(_func.sum(StockLevel.qty_available), 0))
                                           .where(StockLevel.part_id == part.id))).scalar() or 0)
        prev_cost = float(part.avg_cost or part.cost_price or 0)
        new_qty = prev_qty + ln.qty
        if new_qty > 0 and ln.unit_cost:
            part.avg_cost = q4((prev_qty * prev_cost + ln.qty * float(ln.unit_cost)) / new_qty)
        if ln.unit_cost:
            part.cost_price = ln.unit_cost
        # markup prices the product at the moment it lands, from what it actually cost
        if ln.markup_pct is not None and ln.unit_cost:
            new_price = q2(float(ln.unit_cost) * (1 + ln.markup_pct / 100.0))
            if ln.price_type_id:
                from backend.models import PriceTypeItem
                row = (await db.execute(select(PriceTypeItem).where(
                    PriceTypeItem.org_id == current.org_id,
                    PriceTypeItem.price_type_id == ln.price_type_id,
                    PriceTypeItem.item_type == "product",
                    PriceTypeItem.item_id == part.id))).scalar_one_or_none()
                if row:
                    row.price = new_price
                else:
                    db.add(PriceTypeItem(
                        org_id=current.org_id, price_type_id=ln.price_type_id,
                        item_type="product", item_id=part.id, price=new_price))
            else:
                part.sell_price = new_price
        await _give_stock(db, ln.part_id, w, ln.qty, w.location_id)
        # FIFO cost layer, as every other receipt path creates (exact COGS on consumption).
        from backend.models import StockItemBatch
        db.add(StockItemBatch(org_id=current.org_id, part_id=part.id, location_id=w.location_id,
                              unit_cost=cost, qty_received=Decimal(str(ln.qty)),
                              qty_remaining=Decimal(str(ln.qty))))
    else:
        await _take_stock(db, ln.part_id, w, ln.qty, doc="un-post")
        cost = Decimal(str(ln.unit_cost or 0))

    db.add(StockMovement(
        part_id=ln.part_id, location_id=w.location_id, warehouse_id=w.id,
        movement_type="receive" if sign > 0 else "posting_reverted",
        qty=sign * ln.qty, unit_cost=float(cost), created_by_id=current.id))
    return cost * Decimal(str(ln.qty))


@router.get("/postings")
async def list_postings(
    supplier_id: Optional[str] = None,
    warehouse_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    q = select(Posting).where(Posting.org_id == current.org_id, Posting.status != "deleted")
    if supplier_id:
        q = q.where(Posting.supplier_id == supplier_id)
    if warehouse_id:
        q = q.where(Posting.warehouse_id == warehouse_id)
    rows = (await db.execute(q.order_by(Posting.created_at.desc()).limit(200))).scalars().all()
    out = []
    for d in rows:
        lines = (await db.execute(select(PostingLine).where(
            PostingLine.posting_id == d.id))).scalars().all()
        out.append(_ser_posting(d, lines))
    return {"items": out}


@router.post("/postings", status_code=201)
async def create_posting(
    data: PostingIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """Goods-in as a document: stock, moving-average cost, sell price (from markup) and
    the supplier settlement all move together, and the whole thing stays correctable."""
    if not data.lines:
        raise HTTPException(400, "A posting needs at least one line")
    if data.settlement not in ("balance", "cash"):
        raise HTTPException(422, "settlement must be balance|cash")
    for ln in data.lines:
        if ln.qty <= 0:
            raise HTTPException(422, "Quantity must be positive")
    w = await wh.guard_action(db, current, "post",
                              warehouse_id=data.warehouse_id, location_id=data.location_id)
    if not w:
        raise HTTPException(400, "No warehouse resolved — pass warehouse_id")

    from backend.services.numbering import next_number
    try:
        number = await next_number(db, current.org_id, "posting")
    except Exception:
        number = None

    doc = Posting(org_id=current.org_id, number=number, warehouse_id=w.id,
                  supplier_id=data.supplier_id, settlement=data.settlement,
                  account_id=data.account_id, reference=data.reference,
                  note=data.note, created_by_id=current.id)
    db.add(doc)
    await db.flush()

    total = Decimal("0")
    for ln in data.lines:
        line_total = await _apply_posting_line(db, current, w, ln, sign=+1)
        total += line_total
        db.add(PostingLine(
            posting_id=doc.id, part_id=ln.part_id, qty=ln.qty,
            unit_cost=Decimal(str(ln.unit_cost or 0)), markup_pct=ln.markup_pct,
            price_type_id=ln.price_type_id, total=line_total))

    doc.total = total
    if data.settlement == "cash" and data.account_id and total:
        # Paid on the spot: the money leaves the chosen cashbox now (it used to post nothing).
        from backend.routers.cash import post_transaction
        await post_transaction(db, current.org_id, data.account_id, "expense", -float(total),
                               comment=f"Posting {doc.number or ''} (cash)".strip(),
                               ref_type="posting", ref_id=doc.id, employee_id=current.id)
    if data.settlement == "balance" and data.supplier_id:
        sup = (await db.execute(select(Supplier).where(
            Supplier.id == data.supplier_id, Supplier.org_id == current.org_id))).scalar_one_or_none()
        if sup:
            # 144ax: single write-path — goods received on credit, we now owe +total.
            from backend.services.ledger import adjust_supplier_ap
            await adjust_supplier_ap(db, current.org_id, sup, float(total), entry_type="posting",
                                     note=f"Posting {doc.number or ''}".strip(),
                                     ref_type="posting", ref_id=doc.id, created_by_id=current.id)
    await db.commit()
    await db.refresh(doc)
    lines = (await db.execute(select(PostingLine).where(
        PostingLine.posting_id == doc.id))).scalars().all()
    return _ser_posting(doc, lines)


@router.delete("/postings/{doc_id}")
async def delete_posting(
    doc_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """Take the goods back out and undo what we owed. Refused once paid — the money has
    already moved, so the posting has to be corrected, not erased."""
    doc = (await db.execute(select(Posting).where(
        Posting.id == doc_id, Posting.org_id == current.org_id))).scalar_one_or_none()
    if not doc or doc.status == "deleted":
        raise HTTPException(404, "Posting not found")
    if doc.paid:
        raise HTTPException(409, "This posting has been paid. Delete the payment first.")

    w = await wh.resolve(db, current.org_id, warehouse_id=doc.warehouse_id)
    lines = (await db.execute(select(PostingLine).where(
        PostingLine.posting_id == doc.id))).scalars().all()
    # A4: posting freeze — once the posted goods have moved downstream (sold, used,
    # transferred, written off), the on-hand for a line is below what it added, so
    # reversing it would drive stock negative and desync FEFO. Block and point to
    # the correcting document instead of silently erasing history.
    for ln in lines:
        sl = await _stock_row(db, ln.part_id, w)
        have = (sl.qty_available if sl else 0) or 0
        if have < ln.qty:
            name = (await db.execute(select(Part.name).where(Part.id == ln.part_id))).scalar_one_or_none()
            raise HTTPException(
                409, f"“{name}” from this posting has already been used, sold or "
                     f"transferred (only {have:g} of {ln.qty:g} left on hand). Correct it "
                     f"with a write-off or supplier return instead of deleting the posting.")
    for ln in lines:
        await _apply_posting_line(db, current, w, ln, sign=-1)
    doc.status = "deleted"
    if doc.settlement == "cash" and doc.account_id and doc.total:
        from backend.routers.cash import post_transaction
        await post_transaction(db, current.org_id, doc.account_id, "income", float(doc.total),
                               comment=f"Posting {doc.number or ''} deleted (cash back)".strip(),
                               ref_type="posting", ref_id=doc.id, employee_id=current.id)
    if doc.supplier_id:
        sup = (await db.execute(select(Supplier).where(
            Supplier.id == doc.supplier_id, Supplier.org_id == current.org_id))).scalar_one_or_none()
        # Only balance-settled postings ever added to the supplier balance on create, so only
        # those reverse it here — a cash-settled posting never touched sup.balance (otherwise
        # deleting one wrongly drove the supplier negative by doc.total).
        if sup and doc.settlement == "balance":
            # 144ax: single write-path reverses both column + ledger by −total together.
            from backend.services.ledger import adjust_supplier_ap
            await adjust_supplier_ap(db, current.org_id, sup, -float(doc.total or 0), entry_type="posting",
                                     note=f"Posting {doc.number or ''} deleted".strip(),
                                     ref_type="posting", ref_id=doc.id, created_by_id=current.id)
    await db.commit()
    return {"ok": True, "stock_removed": True}


# ══ Build 109: expired-stock suggestions (FEFO clearance) ════════════════════
@router.get("/write-offs/suggestions")
async def write_off_suggestions(
    warehouse_id: Optional[str] = None,
    within_days: int = 0,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Batches that are expired (or expiring within `within_days`) and still have stock —
    a ready-made write-off list. This is the actionable other half of the expiration
    report: instead of just telling you what's going off, it hands you the exact lines,
    earliest-expiry first, to clear in one write-off document.
    """
    from datetime import date, timedelta

    from backend.models import StockItemBatch, Warehouse

    cutoff = date.today() + timedelta(days=max(0, within_days))
    q = (select(StockItemBatch, Part)
         .join(Part, StockItemBatch.part_id == Part.id)
         .where(StockItemBatch.org_id == current.org_id,
                StockItemBatch.qty_remaining > 0,
                StockItemBatch.expiry_date.isnot(None),
                func.date(StockItemBatch.expiry_date) <= cutoff))
    if warehouse_id:
        q = q.where(StockItemBatch.warehouse_id == warehouse_id)
    rows = (await db.execute(q.order_by(StockItemBatch.expiry_date))).all()

    wh_names = {w.id: w.name for w in (await db.execute(select(Warehouse).where(
        Warehouse.org_id == current.org_id))).scalars().all()}
    today = date.today()
    items = []
    for b, p in rows:
        exp = b.expiry_date.date() if hasattr(b.expiry_date, "date") else b.expiry_date
        days = (exp - today).days
        cost = float(b.unit_cost or p.avg_cost or p.cost_price or 0)
        qty_rem = float(b.qty_remaining or 0)
        items.append({
            "batch_id": b.id, "part_id": p.id, "part_name": p.name, "sku": p.sku,
            "warehouse_id": b.warehouse_id, "warehouse_name": wh_names.get(b.warehouse_id, "—"),
            "qty": qty_rem, "expiry_date": exp.isoformat(), "days_left": days,
            "expired": days < 0, "unit_cost": q2(cost),
            "value_at_risk": q2(qty_rem * cost),
        })
    return {"items": items,
            "total_value": q2(sum(i["value_at_risk"] for i in items))}


async def _atomic_add(db, obj, attr, delta, *, round_to=None):
    from backend.services.money import add_to_column
    return await add_to_column(db, obj, attr, delta, round_to=round_to)
