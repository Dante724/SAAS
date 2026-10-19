"""Build 94 — suppliers and purchase-order reservations.

Suppliers were read-only: you could list them but not create one, which made the
supplier balance that postings and returns maintain oddly unreachable. This adds the
CRUD, plus the two things a shop actually does with a supplier:

  * **Import their price list** — the same column-mapping engine the client import uses
    (build 82), pointed at products instead of contacts, so a supplier's spreadsheet
    updates costs without retyping.
  * **Reserve incoming goods for a customer** — a PO line can be tied to the work order
    that's waiting on the part, so when the goods land they're already spoken for
    rather than being sold to whoever walks in first.
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import (
    Employee,
    Order,
    Part,
    POLine,
    PurchaseOrder,
    Supplier,
)
from backend.services.auth import get_current_employee, require_perm
from backend.services.money import q2
from backend.core.spreadsheet import MAX_IMPORT_ROWS

router = APIRouter(prefix="/api/suppliers", tags=["inventory"])


def _ser(s: Supplier) -> dict:
    return {"id": s.id, "name": s.name, "contact_name": s.contact_name,
            "phone": s.phone, "email": s.email, "lead_days": s.lead_days,
            "balance": float(s.balance or 0), "currency": s.currency,
            # Build 144am — the unified Contact this supplier mirrors, so the UI can open
            # the one contact form (with the Client/Supplier role switch) instead of a
            # separate supplier sheet.
            "client_id": s.client_id}


@router.get("")
async def list_suppliers(
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    q = select(Supplier).where(Supplier.org_id == current.org_id)
    if search:
        q = q.where(or_(Supplier.name.icontains(search), Supplier.email.icontains(search)))
    rows = (await db.execute(q.order_by(Supplier.name))).scalars().all()
    return {"items": [_ser(s) for s in rows]}


@router.get("/{supplier_id}/ledger")
async def supplier_ledger(
    supplier_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    """Build 124 — the supplier's Balance tab: the unified contact ledger for the
    contact this supplier mirrors (``Supplier.client_id``). Postings we owe show as
    negative rows, returns as positive, so the running balance is a real, exportable
    statement instead of a bare number."""
    from backend.models import Client, ClientLedgerEntry
    s = (await db.execute(select(Supplier).where(
        Supplier.id == supplier_id, Supplier.org_id == current.org_id))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Supplier not found")
    entries = []
    contact_balance = None
    if s.client_id:
        rows = (await db.execute(select(ClientLedgerEntry).where(
            ClientLedgerEntry.client_id == s.client_id)
            .order_by(ClientLedgerEntry.created_at.desc()).limit(200))).scalars().all()
        entries = [{"id": e.id, "entry_type": e.entry_type, "amount": float(e.amount),
                    "note": e.note, "ref_type": e.ref_type, "ref_id": e.ref_id,
                    "created_at": e.created_at.isoformat()} for e in rows]
        c = (await db.execute(select(Client).where(Client.id == s.client_id))).scalar_one_or_none()
        contact_balance = float(c.balance or 0) if c else None
    # 144aw: the supplier balance (a running column maintained by receipts/returns/postings) and
    # the linked contact's ledger balance are two representations of the same debt and can drift.
    # Surface the gap so it's detectable rather than silent. Sign convention: Supplier.balance is
    # positive when we owe them; the contact ledger stores the same debt as a negative client
    # balance — so a reconciled pair sums to ~0.
    sup_bal = float(s.balance or 0)
    discrepancy = q2(sup_bal + contact_balance) if contact_balance is not None else None
    return {"supplier_balance": sup_bal, "client_id": s.client_id,
            "contact_balance": contact_balance, "entries": entries,
            "discrepancy": discrepancy,
            "reconciled": (discrepancy is None or abs(discrepancy) < 0.01)}


class SupplierPayIn(BaseModel):
    amount: float
    account_id: str                 # cashbox the money leaves
    note: Optional[str] = None


@router.post("/{supplier_id}/pay")
async def pay_supplier(
    supplier_id: str,
    data: SupplierPayIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """144bf — settle what we owe a supplier. Three linked, audited movements in one
    transaction: money leaves a cashbox (expense), the supplier's AP balance drops, and
    the unified contact ledger records the settlement. All reversible via the cash
    transaction's own correction tooling.

    Sign map: Supplier.balance is positive = we owe; the contact ledger is negative = we
    owe (single source in services/ledger). A payment therefore subtracts from
    Supplier.balance and posts a positive contact row that moves the contact toward zero.
    """
    from backend.models import Account
    s = await _load(db, current, supplier_id)
    amount = q2(float(data.amount or 0))
    if amount <= 0:
        raise HTTPException(422, "Payment amount must be positive")
    acc = (await db.execute(select(Account).where(
        Account.id == data.account_id, Account.org_id == current.org_id))).scalar_one_or_none()
    if not acc:
        raise HTTPException(404, "Cash account not found")

    # Atomic decrement (guards against a concurrent double-pay losing an update). No >= guard:
    # overpaying is allowed and simply tips the balance negative (supplier now owes us a credit).
    new_balance = q2(float(s.balance or 0) - amount)
    await db.execute(sa_update(Supplier).where(
        Supplier.id == s.id, Supplier.org_id == current.org_id).values(balance=Supplier.balance - amount))

    note = (data.note or f"Payment to {s.name}").strip()
    from backend.routers.cash import post_transaction
    await post_transaction(db, current.org_id, acc.id, "expense", -amount,
                           comment=note, ref_type="supplier_payment", ref_id=s.id,
                           employee_id=current.id)
    # Settle the mirrored contact ledger so the Balance tab statement stays honest.
    if s.client_id:
        from backend.services.ledger import post_contact_ledger
        await post_contact_ledger(db, current.org_id, s.client_id, "payout", amount,
                                  note=note, ref_type="supplier_payment", ref_id=s.id,
                                  created_by_id=current.id)
    await db.commit()
    return {"ok": True, "supplier_id": s.id, "paid": amount, "balance": new_balance}


@router.get("/{supplier_id}/performance")
async def supplier_performance(
    supplier_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Supplier scorecard from PO history: how much we spend, how reliably they deliver
    (on-time %), and how their prices are trending. This is the analytics Orderry doesn't
    give you about the people you buy from."""
    s = (await db.execute(select(Supplier).where(
        Supplier.id == supplier_id, Supplier.org_id == current.org_id))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Supplier not found")
    pos = (await db.execute(select(PurchaseOrder).where(PurchaseOrder.supplier_id == supplier_id))).scalars().all()
    total = len(pos)
    received = [p for p in pos if p.status == "received"]
    open_pos = [p for p in pos if p.status in ("draft", "ordered", "partial")]
    spend = q2(sum(float(p.received_cost or 0) for p in pos))
    # on-time: received on/before the expected date, among received POs that had an expected date
    dated = [p for p in received if p.expected_at and p.received_at]
    on_time = [p for p in dated if p.received_at <= p.expected_at]
    on_time_pct = round(100 * len(on_time) / len(dated), 1) if dated else None
    last_order = max((p.ordered_at for p in pos if p.ordered_at), default=None)
    # price trend: first vs latest unit cost per part across this supplier's PO lines
    po_ids = [p.id for p in pos]
    trend = []
    if po_ids:
        lines = (await db.execute(select(POLine).where(POLine.po_id.in_(po_ids)))).scalars().all()
        po_time = {p.id: (p.ordered_at or p.created_at) for p in pos}
        by_part: dict = {}
        for l in lines:
            by_part.setdefault(l.part_id, []).append((po_time.get(l.po_id), l.unit_cost or 0))
        part_names = {p.id: p.name for p in (await db.execute(
            select(Part).where(Part.org_id == current.org_id))).scalars().all()}
        for pid, seq in by_part.items():
            seq = [x for x in seq if x[0] is not None]
            if len(seq) < 2:
                continue
            seq.sort(key=lambda x: x[0])
            first, latest = seq[0][1], seq[-1][1]
            if first and latest != first:
                trend.append({"part": part_names.get(pid, pid), "first": q2(first),
                              "latest": q2(latest),
                              "change_pct": round(100 * (latest - first) / first, 1)})
        trend.sort(key=lambda t: abs(t["change_pct"]), reverse=True)
    return {"total_pos": total, "received_pos": len(received), "open_pos": len(open_pos),
            "total_spend": spend, "on_time_pct": on_time_pct, "on_time_n": len(dated),
            "last_order_at": last_order.isoformat() if last_order else None,
            "balance": float(s.balance or 0), "price_trend": trend[:6]}


class SupplierIn(BaseModel):
    name: str
    contact_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    lead_days: Optional[int] = None
    currency: Optional[str] = None


@router.post("", status_code=201)
async def create_supplier(
    data: SupplierIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    # only set what was actually given — passing None would clobber the column defaults
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    # Build 114: a supplier is also a contact. Create (or reuse) a unified Client marked
    # is_supplier so the same entity shows up in Contacts and can carry a balance/history.
    from backend.models import Client
    contact = None
    # Match only on the identifiers actually provided — comparing a NULL phone/email would
    # translate to `IS NULL` and wrongly attach the supplier to any contact missing that field.
    match_terms = []
    if data.phone:
        match_terms.append(Client.phone == data.phone)
    if data.email:
        match_terms.append(Client.email == data.email)
    if match_terms:
        contact = (await db.execute(select(Client).where(
            Client.org_id == current.org_id, or_(*match_terms)))).scalars().first()
    if not contact:
        contact = Client(org_id=current.org_id, name=data.name, phone=data.phone,
                         email=data.email, is_supplier=True)
        db.add(contact)
        await db.flush()
    elif not contact.is_supplier:
        contact.is_supplier = True
    s = Supplier(org_id=current.org_id, balance=0, client_id=contact.id, **fields)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return _ser(s)


@router.put("/{supplier_id}")
async def update_supplier(
    supplier_id: str, data: SupplierIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    s = await _load(db, current, supplier_id)
    for k, v in data.model_dump().items():
        if v is not None:
            setattr(s, k, v)
    await db.commit()
    return _ser(s)


@router.delete("/{supplier_id}")
async def delete_supplier(
    supplier_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """A supplier we still owe (or who owes us) can't be deleted — the balance is a real
    obligation and deleting the counterparty wouldn't settle it."""
    s = await _load(db, current, supplier_id)
    if s.balance:
        raise HTTPException(
            409, f"This supplier has a balance of {float(s.balance):g}. Settle it first.")
    open_po = (await db.execute(select(PurchaseOrder.id).where(
        PurchaseOrder.supplier_id == supplier_id,
        PurchaseOrder.status.notin_(["received", "canceled", "cancelled"])))).scalars().first()
    if open_po:
        raise HTTPException(409, "This supplier has open purchase orders")
    await db.delete(s)
    await db.commit()
    return {"ok": True}


# ── price-list import ────────────────────────────────────────────────────────
class PriceListIn(BaseModel):
    rows: list[dict] = Field(max_length=MAX_IMPORT_ROWS)
    mapping: dict[str, str]        # {source_column: field}  field ∈ IMPORTABLE
    match_on: str = "sku"          # sku|barcode
    create_missing: bool = False   # add products the price list has and we don't
    dry_run: bool = False


IMPORTABLE = {"sku", "barcode", "name", "cost_price", "sell_price"}


@router.post("/{supplier_id}/import-price-list")
async def import_price_list(
    supplier_id: str, data: PriceListIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """Update product costs from a supplier's file.

    Matching is on SKU or barcode — never on name, which is not stable enough to key on.
    `dry_run` shows exactly what would change first, because a bad mapping here would
    rewrite the cost basis of the whole catalogue.
    """
    await _load(db, current, supplier_id)
    unknown = set(data.mapping.values()) - IMPORTABLE
    if unknown:
        raise HTTPException(422, f"Can't map to unknown fields: {sorted(unknown)}")
    if data.match_on not in ("sku", "barcode"):
        raise HTTPException(422, "match_on must be sku|barcode")
    if data.match_on not in data.mapping.values():
        raise HTTPException(422, f"Map a column to “{data.match_on}” so rows can be matched")

    updated = created = skipped = 0
    errors: list[dict] = []
    changes: list[dict] = []

    for i, raw in enumerate(data.rows):
        rec = {}
        for src, field in data.mapping.items():
            v = raw.get(src)
            if v not in (None, ""):
                rec[field] = v
        key = rec.get(data.match_on)
        if not key:
            errors.append({"row": i + 1, "error": f"Missing {data.match_on}"})
            skipped += 1
            continue

        for money in ("cost_price", "sell_price"):
            if money in rec:
                try:
                    rec[money] = float(str(rec[money]).replace(",", "").strip())
                except ValueError:
                    errors.append({"row": i + 1, "error": f"{money} isn't a number"})
                    rec.pop(money)

        col = Part.sku if data.match_on == "sku" else Part.barcode
        part = (await db.execute(select(Part).where(
            Part.org_id == current.org_id, col == str(key)))).scalars().first()

        if not part:
            if not data.create_missing:
                skipped += 1
                continue
            created += 1
            changes.append({"row": i + 1, "action": "create", "key": key})
            if not data.dry_run:
                db.add(Part(org_id=current.org_id, name=rec.get("name") or f"Item {key}",
                            sku=rec.get("sku"), barcode=rec.get("barcode"),
                            cost_price=rec.get("cost_price", 0),
                            sell_price=rec.get("sell_price", 0)))
            continue

        delta = {k: v for k, v in rec.items()
                 if k in ("cost_price", "sell_price", "name")
                 and getattr(part, k, None) != v}
        if not delta:
            skipped += 1
            continue
        updated += 1
        changes.append({"row": i + 1, "action": "update", "key": key,
                        "part_id": part.id, "changes": delta})
        if not data.dry_run:
            for k, v in delta.items():
                setattr(part, k, v)

    if not data.dry_run:
        await db.commit()
    return {"ok": True, "dry_run": data.dry_run, "updated": updated, "created": created,
            "skipped": skipped, "errors": errors, "changes": changes[:100]}


# ── Build 94: reserve incoming goods for a customer order ────────────────────
class ReserveIn(BaseModel):
    order_id: str


@router.post("/purchase-orders/{po_id}/lines/{line_id}/reserve")
async def reserve_po_line(
    po_id: str, line_id: str, data: ReserveIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    """Tie an incoming PO line to the work order that's waiting on the part.

    Reserving *before* the goods arrive is the point: when they land, they're already
    spoken for, instead of being sold over the counter to the next person through the
    door while the customer who's been waiting a week gets told "still on order".
    """
    po = (await db.execute(select(PurchaseOrder).join(Supplier, PurchaseOrder.supplier_id == Supplier.id).where(
        PurchaseOrder.id == po_id, Supplier.org_id == current.org_id))).scalar_one_or_none()
    if not po:
        raise HTTPException(404, "Purchase order not found")
    ln = (await db.execute(select(POLine).where(
        POLine.id == line_id, POLine.po_id == po_id))).scalar_one_or_none()
    if not ln:
        raise HTTPException(404, "Line not found on this purchase order")
    order = (await db.execute(select(Order).where(
        Order.id == data.order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Work order not found")
    if po.status == "received":
        raise HTTPException(409, "These goods have already arrived — reserve from stock instead")

    ln.reserved_order_id = order.id
    await db.commit()
    return {"ok": True, "line_id": ln.id, "reserved_for": order.order_number,
            "expected_at": po.expected_at.isoformat() if po.expected_at else None}


@router.delete("/purchase-orders/{po_id}/lines/{line_id}/reserve")
async def unreserve_po_line(
    po_id: str, line_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    po = (await db.execute(select(PurchaseOrder).join(Supplier, PurchaseOrder.supplier_id == Supplier.id).where(
        PurchaseOrder.id == po_id, Supplier.org_id == current.org_id))).scalar_one_or_none()
    if not po:
        raise HTTPException(404, "Purchase order not found")
    ln = (await db.execute(select(POLine).where(
        POLine.id == line_id, POLine.po_id == po_id))).scalar_one_or_none()
    if not ln:
        raise HTTPException(404, "Line not found")
    ln.reserved_order_id = None
    await db.commit()
    return {"ok": True}


# NOTE: PO receiving lives at `POST /api/inventory/po/{id}/receive` — the single path
# that posts stock, moving-average cost, FIFO layers, landed cost, supplier balance AND
# honours reservations. A second, stock-less receive endpoint used to live here; it was
# removed (144aab) because it flipped a PO to "received" without ever moving inventory.


@router.get("/purchase-orders/incoming")
async def incoming_stock(
    part_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """What's on the way, when it's due, and whether it's already promised to someone.

    A PO whose expected date has passed is reported as **delayed** — a date that has
    quietly gone by is exactly the thing a shop needs told, not left to notice.
    """
    q = (select(PurchaseOrder)
         .join(Supplier, PurchaseOrder.supplier_id == Supplier.id)
         .where(Supplier.org_id == current.org_id,
                PurchaseOrder.status.notin_(["received", "canceled", "cancelled"])))
    pos = (await db.execute(q)).scalars().all()
    today = date.today()

    items = []
    for po in pos:
        lines = (await db.execute(select(POLine).where(POLine.po_id == po.id))).scalars().all()
        for ln in lines:
            if part_id and ln.part_id != part_id:
                continue
            outstanding = (ln.qty_ordered or 0) - (ln.qty_received or 0)
            if outstanding <= 0:
                continue
            expected = po.expected_at.date() if po.expected_at else None
            order_no = None
            if ln.reserved_order_id:
                order_no = (await db.execute(select(Order.order_number).where(
                    Order.id == ln.reserved_order_id))).scalar_one_or_none()
            items.append({
                "po_id": po.id, "po_number": po.po_number, "line_id": ln.id,
                "part_id": ln.part_id, "qty_incoming": outstanding,
                "expected_at": expected.isoformat() if expected else None,
                "delayed": bool(expected and expected < today),
                "reserved_order_id": ln.reserved_order_id,
                "reserved_for": order_no,
            })
    return {"items": items}


async def _load(db: AsyncSession, current: Employee, supplier_id: str) -> Supplier:
    s = (await db.execute(select(Supplier).where(
        Supplier.id == supplier_id, Supplier.org_id == current.org_id))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Supplier not found")
    return s
