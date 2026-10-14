from fastapi import APIRouter, Depends, HTTPException, Query, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from sqlalchemy.orm import selectinload
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, date
from backend.db.database import get_db
from backend.models import Invoice, Payment, CashShift, CashDrawerMovement, Expense, Order, Employee, Location
from backend.services.auth import get_current_employee, require_perm, record_scope
from backend.services.money import q2, q4, msum  # noqa: F401 — money quantizers used module-wide

router = APIRouter(prefix="/api/finance", tags=["finance"],
                   dependencies=[Depends(record_scope)])   # role record scope on {order_id}/{client_id}

class InvoiceLineIn(BaseModel):
    kind: str = "custom"                            # service|product|labor|custom
    source_id: Optional[str] = None                 # catalog ref (null for hand-typed)
    description: str = ""
    qty: float = 1.0
    unit_price: float = 0.0

class InvoiceIn(BaseModel):
    order_id: Optional[str] = None
    order_ids: list[str] = []                       # 144ed: bill several orders at create
    client_id: Optional[str] = None
    payer_client_id: Optional[str] = None          # 144dr: bill a different party
    display_mode: str = "detailed"                  # 144dr: detailed|simplified|one_line
    subtotal: float = 0.0
    discount: float = 0.0
    tax: float = 0.0
    comment: str = ""                               # 144ed
    issued_at: Optional[str] = None                 # 144ed: ISO date
    due_at: Optional[str] = None                    # 144ed: ISO date
    lines: list[InvoiceLineIn] = []                 # 144ed: free-form catalog/manual lines

class PaymentIn(BaseModel):
    method: str  # cash|card|transfer|balance|online
    amount: float
    reference: Optional[str] = None
    shift_id: Optional[str] = None
    account_id: Optional[str] = None   # 144av: cashbox the money lands in (non-balance methods)

class ShiftOpen(BaseModel):
    location_id: str
    opening_float: float = 0.0
    denoms: Optional[dict] = None          # 144bc: optional {"100": 2, "20": 5, ...} count


class DrawerMoveIn(BaseModel):
    direction: str                          # in | out
    amount: float
    note: str


class ShiftCloseIn(BaseModel):
    closing_float: Optional[float] = None   # or derive from denoms
    denoms: Optional[dict] = None

class ExpenseIn(BaseModel):
    description: str
    amount: float
    category: Optional[str] = None
    location_id: Optional[str] = None
    expense_date: Optional[str] = None

def _inv_number(seq: int) -> str:
    return f"INV-{seq:05d}"

def _ser_invoice(inv: Invoice) -> dict:
    return {
        "id": inv.id, "invoice_number": inv.invoice_number,
        "status": inv.status, "subtotal": inv.subtotal,
        "discount": inv.discount, "tax": inv.tax, "total": inv.total,
        "amount_paid": inv.amount_paid,
        "balance_due": q2(inv.total - inv.amount_paid),
        "issued_at": inv.issued_at.isoformat() if inv.issued_at else None,
        "due_at": inv.due_at.isoformat() if getattr(inv, "due_at", None) else None,
        "comment": getattr(inv, "comment", "") or "",
        "order_id": inv.order_id, "client_id": inv.client_id,
        "payer_client_id": getattr(inv, "payer_client_id", None),
        "display_mode": getattr(inv, "display_mode", "detailed"),
        "auto_drafted": getattr(inv, "auto_drafted", False),
        # 144if — e-invoicing status (XML fetched via the einvoice.xml endpoint, not inlined)
        "einvoice_status": getattr(inv, "einvoice_status", "none") or "none",
        "einvoice_number": getattr(inv, "einvoice_number", None),
        "einvoice_format": getattr(inv, "einvoice_format", None),
        "einvoice_issued_at": inv.einvoice_issued_at.isoformat() if getattr(inv, "einvoice_issued_at", None) else None,
        "payments": [{"method": p.method, "amount": p.amount,
                      "paid_at": p.paid_at.isoformat()} for p in inv.payments],
    }

async def _invoice_detail(db, inv) -> dict:
    """Build 144dt/144du — invoice header + resolved display lines + the work
    orders it bills."""
    from backend.models import Client
    from backend.services.invoicing import display_lines, invoice_orders
    out = _ser_invoice(inv)
    out["lines"] = await display_lines(db, inv)
    out["orders"] = [{"id": o.id, "order_number": o.order_number,
                      "complaint": getattr(o, "complaint", None),
                      "total": float(o.total or 0)} for o in await invoice_orders(db, inv)]
    # 144dw — resolve payer + customer names so the UI can show "Bill to"
    for key, cid in (("payer", getattr(inv, "payer_client_id", None)), ("customer", inv.client_id)):
        if cid:
            c = (await db.execute(select(Client).where(Client.id == cid))).scalar_one_or_none()
            out[key] = {"id": c.id, "name": c.name, "email": c.email} if c else None
        else:
            out[key] = None
    return out

@router.get("/invoices")
async def list_invoices(
    status: Optional[str] = None, location_id: Optional[str] = None, skip: int = 0, limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    from backend.models import Order
    q = (select(Invoice).where(Invoice.org_id == current.org_id)
         .options(selectinload(Invoice.payments))
         .order_by(Invoice.created_at.desc()))
    cq = select(func.count()).select_from(Invoice).where(Invoice.org_id == current.org_id)
    if status:
        q = q.where(Invoice.status == status)
        cq = cq.where(Invoice.status == status)
    if location_id:
        # build 139: scope invoices by the location of the order they bill
        _oids = select(Order.id).where(Order.org_id == current.org_id, Order.location_id == location_id)
        q = q.where(Invoice.order_id.in_(_oids))
        cq = cq.where(Invoice.order_id.in_(_oids))
    result = await db.execute(q.offset(skip).limit(limit))
    invs = result.scalars().all()
    total = (await db.execute(cq)).scalar()
    # Build 144dz — resolve each invoice's primary order number (batched, no N+1) so
    # the list shows WO-00001 instead of a raw UUID.
    oids = [i.order_id for i in invs if i.order_id]
    numbers = {}
    if oids:
        rows = (await db.execute(select(Order.id, Order.order_number).where(Order.id.in_(oids)))).all()
        numbers = {oid: num for oid, num in rows}
    items = []
    for i in invs:
        d = _ser_invoice(i)
        d["order_number"] = numbers.get(i.order_id)
        items.append(d)
    return {"total": total, "items": items}

@router.get("/invoices/{invoice_id}")
async def get_invoice(
    invoice_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Build 144dt — single invoice with its lines resolved through the
    Order-display mode (detailed / simplified / one_line). Lines are computed
    on the fly so switching the mode never rewrites stored data."""
    inv = (await db.execute(
        select(Invoice).where(Invoice.id == invoice_id, Invoice.org_id == current.org_id)
        .options(selectinload(Invoice.payments))
    )).scalar_one_or_none()
    if not inv:
        raise HTTPException(404, "Invoice not found")
    return await _invoice_detail(db, inv)

# ── 144if — e-invoicing (in-house UBL 2.1) ──────────────────────────────────
_EINV_STATUSES = {"cleared", "rejected", "cancelled", "issued"}


def _ser_einvoice(inv: Invoice) -> dict:
    """Minimal e-invoice status payload — avoids touching the lazy payments relationship
    after a commit (which would trigger sync IO in the async session)."""
    return {
        "id": inv.id, "invoice_number": inv.invoice_number,
        "einvoice_status": inv.einvoice_status or "none",
        "einvoice_number": inv.einvoice_number,
        "einvoice_format": inv.einvoice_format,
        "einvoice_issued_at": inv.einvoice_issued_at.isoformat() if inv.einvoice_issued_at else None,
    }


async def _load_invoice(db, invoice_id, org_id) -> Invoice:
    inv = (await db.execute(select(Invoice).where(
        Invoice.id == invoice_id, Invoice.org_id == org_id))).scalar_one_or_none()
    if not inv:
        raise HTTPException(404, "Invoice not found")
    return inv


async def _next_einvoice_number(db, org_id: str) -> str:
    """Sequential fiscal number per org: EINV-00001, 00002, …"""
    n = (await db.execute(select(func.count()).select_from(Invoice).where(
        Invoice.org_id == org_id, Invoice.einvoice_status != "none"))).scalar() or 0
    return f"EINV-{n + 1:05d}"


@router.post("/invoices/{invoice_id}/einvoice")
async def issue_einvoice(
    invoice_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_edit_invoices")),
):
    """Generate the UBL 2.1 e-invoice, assign a fiscal number, store the XML, mark issued.
    Idempotent-ish: re-issuing regenerates the XML but keeps the existing fiscal number."""
    from backend.services.einvoice import build_ubl
    inv = await _load_invoice(db, invoice_id, current.org_id)
    if not inv.einvoice_number:
        inv.einvoice_number = await _next_einvoice_number(db, current.org_id)
    inv.einvoice_issued_at = datetime.utcnow()
    inv.einvoice_format = "ubl2.1"
    inv.einvoice_xml = await build_ubl(db, current.org_id, inv)
    inv.einvoice_status = "issued"
    await db.commit()
    return _ser_einvoice(inv)


class EInvoiceStatusIn(BaseModel):
    status: str                      # cleared | rejected | cancelled | issued
    reference: Optional[str] = None  # external clearance ref, when the authority returns one


@router.patch("/invoices/{invoice_id}/einvoice")
async def set_einvoice_status(
    invoice_id: str,
    data: EInvoiceStatusIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Record the clearance outcome manually (no live authority connection in-house)."""
    if data.status not in _EINV_STATUSES:
        raise HTTPException(422, "Invalid e-invoice status")
    inv = await _load_invoice(db, invoice_id, current.org_id)
    if inv.einvoice_status == "none":
        raise HTTPException(409, "Issue the e-invoice before setting a clearance status")
    inv.einvoice_status = data.status
    if data.reference:
        inv.einvoice_number = data.reference
    await db.commit()
    return _ser_einvoice(inv)


@router.get("/invoices/{invoice_id}/einvoice.xml")
async def download_einvoice(
    invoice_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    from fastapi import Response
    inv = await _load_invoice(db, invoice_id, current.org_id)
    if not inv.einvoice_xml:
        raise HTTPException(404, "No e-invoice issued yet")
    fname = f"einvoice-{inv.einvoice_number or inv.invoice_number or invoice_id[:8]}.xml"
    return Response(content=inv.einvoice_xml, media_type="application/xml",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@router.patch("/invoices/{invoice_id}")
async def update_invoice(
    invoice_id: str, data: dict,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_edit_invoices")),
):
    """Build 144dt/144dw — change an invoice's Order-display mode and/or its payer
    (bill-to). Lines re-resolve on the next GET; stored line data is never rewritten."""
    from backend.models import Client
    inv = (await db.execute(
        select(Invoice).where(Invoice.id == invoice_id, Invoice.org_id == current.org_id)
        .options(selectinload(Invoice.payments))
    )).scalar_one_or_none()
    if not inv:
        raise HTTPException(404, "Invoice not found")
    mode = data.get("display_mode")
    if mode in ("detailed", "simplified", "one_line"):
        inv.display_mode = mode
    # 144dw — set/clear the payer (must be a client in this org). "" or null clears it.
    if "payer_client_id" in data:
        pid = data.get("payer_client_id") or None
        if pid:
            ok = (await db.execute(select(Client.id).where(
                Client.id == pid, Client.org_id == current.org_id))).scalar_one_or_none()
            if not ok:
                raise HTTPException(404, "Payer client not found")
        inv.payer_client_id = pid
    await db.commit()
    await db.refresh(inv)
    return await _invoice_detail(db, inv)

@router.post("/invoices", status_code=201)
async def create_invoice(
    data: InvoiceIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_create_invoices")),
):
    from backend.models import InvoiceOrder, InvoiceLine, Order
    count = (await db.execute(
        select(func.count()).select_from(Invoice).where(Invoice.org_id == current.org_id)
    )).scalar() or 0

    # 144ed — the order list: explicit order_ids, with the legacy single order_id
    # folded in first and de-duped while preserving order.
    order_ids: list[str] = []
    for oid in ([data.order_id] if data.order_id else []) + list(data.order_ids or []):
        if oid and oid not in order_ids:
            order_ids.append(oid)
    # validate they belong to this org
    if order_ids:
        valid = set((await db.execute(select(Order.id).where(
            Order.id.in_(order_ids), Order.org_id == current.org_id))).scalars().all())
        order_ids = [o for o in order_ids if o in valid]

    # 144ed — free-form line amounts + the orders' own totals feed the subtotal
    # when the caller didn't pass one explicitly.
    lines_sum = sum(q2((l.qty or 0) * (l.unit_price or 0)) for l in (data.lines or []))
    orders_sum = 0.0
    if order_ids:
        from backend.services.invoicing import _order_amount
        rows = (await db.execute(select(Order).where(Order.id.in_(order_ids)))).scalars().all()
        orders_sum = sum(_order_amount(o) for o in rows)
    subtotal = data.subtotal if data.subtotal else msum([lines_sum, orders_sum])
    # H2 Phase 1: money round-half-up (q2), not Python's banker's rounding, so discounted/taxed
    # invoice totals match what a receipt should show to the penny.
    total = q2(subtotal * (1 - data.discount / 100) * (1 + data.tax / 100))

    def _pdate(s):
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "").split("T")[0])
        except Exception:
            return None

    inv = Invoice(
        invoice_number=_inv_number(count + 1),
        org_id=current.org_id,
        order_id=order_ids[0] if order_ids else None,
        client_id=data.client_id,
        payer_client_id=data.payer_client_id,
        display_mode=data.display_mode if data.display_mode in ("detailed", "simplified", "one_line") else "detailed",
        comment=(data.comment or "")[:1000],
        status="draft",
        subtotal=subtotal,
        discount=data.discount,
        tax=data.tax,
        total=total,
        amount_paid=0.0,
        issued_at=_pdate(data.issued_at) or datetime.utcnow(),
        due_at=_pdate(data.due_at),
    )
    db.add(inv)
    await db.flush()
    # 144du/144ed — seed the multi-order link table (every billed order)
    for i, oid in enumerate(order_ids):
        db.add(InvoiceOrder(invoice_id=inv.id, order_id=oid, sort_order=i))
    # 144ed — persist free-form catalog/manual lines
    for i, l in enumerate(data.lines or []):
        db.add(InvoiceLine(
            invoice_id=inv.id, order_line_id=None,
            kind=(l.kind or "custom")[:16], source_id=l.source_id,
            sort_order=i, description=(l.description or "")[:500],
            qty=l.qty or 0, unit_price=l.unit_price or 0,
            total=q2((l.qty or 0) * (l.unit_price or 0))))
    await db.commit()
    await db.refresh(inv)
    return {"id": inv.id, "invoice_number": inv.invoice_number, "total": inv.total}


@router.post("/invoices/{invoice_id}/orders")
async def attach_order(
    invoice_id: str, data: dict,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_edit_invoices")),
):
    """Build 144du — bill another work order on this invoice (Orderry parity)."""
    from backend.models import InvoiceOrder, Order
    inv = (await db.execute(select(Invoice).where(
        Invoice.id == invoice_id, Invoice.org_id == current.org_id))).scalar_one_or_none()
    if not inv:
        raise HTTPException(404, "Invoice not found")
    order_id = data.get("order_id")
    order = (await db.execute(select(Order).where(
        Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    exists = (await db.execute(select(InvoiceOrder).where(
        InvoiceOrder.invoice_id == invoice_id, InvoiceOrder.order_id == order_id))).scalar_one_or_none()
    if not exists:
        n = (await db.execute(select(func.count()).select_from(InvoiceOrder)
             .where(InvoiceOrder.invoice_id == invoice_id))).scalar() or 0
        db.add(InvoiceOrder(invoice_id=invoice_id, order_id=order_id, sort_order=n))
        if not inv.order_id:
            inv.order_id = order_id
        await db.commit()
    inv = (await db.execute(select(Invoice).where(Invoice.id == invoice_id)
           .options(selectinload(Invoice.payments)))).scalar_one()
    return await _invoice_detail(db, inv)


@router.delete("/invoices/{invoice_id}/orders/{order_id}")
async def detach_order(
    invoice_id: str, order_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_edit_invoices")),
):
    """Build 144du — stop billing a work order on this invoice."""
    from backend.models import InvoiceOrder
    inv = (await db.execute(select(Invoice).where(
        Invoice.id == invoice_id, Invoice.org_id == current.org_id))).scalar_one_or_none()
    if not inv:
        raise HTTPException(404, "Invoice not found")
    link = (await db.execute(select(InvoiceOrder).where(
        InvoiceOrder.invoice_id == invoice_id, InvoiceOrder.order_id == order_id))).scalar_one_or_none()
    if link:
        await db.delete(link)
        if inv.order_id == order_id:
            nxt = (await db.execute(select(InvoiceOrder).where(
                InvoiceOrder.invoice_id == invoice_id, InvoiceOrder.order_id != order_id)
                .order_by(InvoiceOrder.sort_order))).scalars().first()
            inv.order_id = nxt.order_id if nxt else None
        await db.commit()
    return {"ok": True}

class SendInvoiceIn(BaseModel):
    to: Optional[str] = None       # override recipient; defaults to payer/client email
    subject: Optional[str] = None
    message: Optional[str] = None

@router.post("/invoices/{invoice_id}/send")
async def send_invoice_email(
    invoice_id: str, data: SendInvoiceIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    """Build 144dw — email the invoice PDF to the client (Orderry's envelope button).
    Uses the org's connected email account; if none is connected the send is
    'simulated' (logged) rather than failing, matching the app's gated-seam pattern.
    A record is written to the client's inbox thread either way."""
    from backend.models import Client, EmailConnection
    from backend.routers.printing import _org_lang, _org_name
    from backend.services.email_oauth import send_via_connection
    from backend.services.invoicing import build_invoice_pdf

    inv = (await db.execute(select(Invoice).where(
        Invoice.id == invoice_id, Invoice.org_id == current.org_id))).scalar_one_or_none()
    if not inv:
        raise HTTPException(404, "Invoice not found")

    # recipient: explicit override, else payer, else customer email
    recipient = (data.to or "").strip() or None
    bill_to_id = getattr(inv, "payer_client_id", None) or inv.client_id
    client = None
    if bill_to_id:
        client = (await db.execute(select(Client).where(Client.id == bill_to_id))).scalar_one_or_none()
    if not recipient and client:
        recipient = client.email
    if not recipient:
        raise HTTPException(422, "No recipient email — add one to the client or pass 'to'.")

    pdf = await build_invoice_pdf(db, inv, org_name=await _org_name(db, current.org_id),
                                  lang=await _org_lang(db, current.org_id))
    subject = data.subject or f"Invoice {inv.invoice_number}"
    body = data.message or (f"Please find attached invoice {inv.invoice_number} "
                            f"for {float(inv.total or 0):.2f}. Thank you.")
    fname = f"invoice-{inv.invoice_number or invoice_id[:8]}.pdf"

    conn = (await db.execute(select(EmailConnection).where(
        EmailConnection.org_id == current.org_id, EmailConnection.connected == True)  # noqa: E712
        .order_by(EmailConnection.is_default.desc()))).scalars().first()
    if conn:
        result = await send_via_connection(conn, recipient=recipient, subject=subject,
                                           body=body, attachment=(fname, pdf, "application/pdf"))
    else:
        result = {"status": "simulated", "error": None}

    # log to the client's inbox thread so there's a record it went out
    if client:
        from backend.models import InboxMessage, InboxThread
        thread = (await db.execute(select(InboxThread).where(
            InboxThread.org_id == current.org_id, InboxThread.client_id == client.id,
            InboxThread.channel == "email").order_by(InboxThread.last_message_at.desc()))).scalars().first()
        if not thread:
            thread = InboxThread(org_id=current.org_id, channel="email", client_id=client.id,
                                 handle=recipient, status="open", unread=False,
                                 last_message_at=datetime.utcnow())
            db.add(thread)
            await db.flush()
        db.add(InboxMessage(org_id=current.org_id, thread_id=thread.id, direction="outbound",
                            body=f"{subject}\n\n{body} (attached {fname})",
                            author_id=current.id, delivered=result["status"] in ("sent", "simulated")))
        thread.last_message_at = datetime.utcnow()
        await db.commit()

    return {"ok": result["status"] in ("sent", "simulated"), "status": result["status"],
            "error": result.get("error"), "recipient": recipient}

@router.post("/invoices/{invoice_id}/pay")
async def record_payment(
    invoice_id: str, data: PaymentIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    # Lock the invoice row so concurrent payments serialize (Postgres) — prevents a double-click /
    # retry from double-posting. No-op on SQLite, which serializes writes anyway.
    result = await db.execute(
        select(Invoice).where(Invoice.id == invoice_id, Invoice.org_id == current.org_id)
        .options(selectinload(Invoice.payments)).with_for_update(of=Invoice)
    )
    inv = result.scalar_one_or_none()
    if not inv:
        raise HTTPException(404, "Invoice not found")
    # Negative amounts used to post +|amount| into the cashbox while reducing amount_paid; an
    # amount above the balance silently over-collected.
    if data.amount is None or data.amount <= 0:
        raise HTTPException(400, "Payment amount must be positive")
    remaining = q2(float(inv.total or 0) - float(inv.amount_paid or 0))
    if data.amount > remaining + 0.005:
        raise HTTPException(400, f"Payment exceeds the invoice balance ({remaining:g})")
    payment = Payment(
        invoice_id=invoice_id,
        order_id=inv.order_id,          # so the order's own paid/balance math sees invoice payments
        client_id=inv.client_id,
        method=data.method,
        amount=data.amount,
        reference=data.reference,
        shift_id=data.shift_id,
        account_id=data.account_id,
        received_by_id=current.id,
    )
    db.add(payment)
    await db.flush()   # need payment.id for the ledger ref
    # 144av: real money (cash/card/transfer) must land in a cashbox account, not just move the
    # client's balance. 'balance' settles on account (no cash moves) → no Transaction.
    if data.account_id and data.method != "balance":
        from backend.routers.cash import post_transaction
        await post_transaction(db, current.org_id, data.account_id, "invoice_payment", abs(data.amount),
                               comment=f"Payment for invoice {inv.invoice_number or invoice_id}",
                               ref_type="invoice", ref_id=invoice_id, employee_id=current.id)
    inv.amount_paid = q2(inv.amount_paid + data.amount)
    if inv.amount_paid >= inv.total:
        inv.status = "paid"
    else:
        inv.status = "partial"
    # move the client's balance through the shared ledger, same as the cash.py payment
    # paths — otherwise the Balance tab and Client.balance drift from what was paid.
    if inv.client_id:
        from backend.services.ledger import post_contact_ledger
        await post_contact_ledger(db, current.org_id, inv.client_id, "income", -abs(data.amount),
                                  note=f"Payment for invoice {inv.invoice_number or invoice_id}",
                                  ref_type="payment", ref_id=payment.id, created_by_id=current.id)
    if inv.order_id:
        from backend.services.order_money import cancel_open_pay_links
        order = (await db.execute(select(Order).where(Order.id == inv.order_id))).scalar_one_or_none()
        if order:
            await db.flush()
            await cancel_open_pay_links(db, order)
    await db.commit()
    return {"ok": True, "amount_paid": inv.amount_paid, "status": inv.status}

def _denoms_total(d: Optional[dict]) -> float:
    """Sum a {denomination: count} map, e.g. {"100": 2, "20": 5} → 300.0. Ignores junk keys."""
    if not d:
        return 0.0
    total = 0.0
    for k, n in d.items():
        try:
            total += float(k) * float(n)
        except (TypeError, ValueError):
            continue
    return q2(total)


async def _shift_or_404(db, org_id, shift_id) -> CashShift:
    shift = (await db.execute(select(CashShift).join(Location, CashShift.location_id == Location.id)
             .where(CashShift.id == shift_id, Location.org_id == org_id))).scalar_one_or_none()
    if not shift:
        raise HTTPException(404, "Shift not found")
    return shift


async def _expected_cash(db, shift: CashShift) -> dict:
    """Breakdown of what should physically be in the drawer: opening float + cash payments −
    cash refunds + manual pay-ins − pay-outs, all on this shift. Returns the parts so the close
    can explain a discrepancy."""
    cash_in = (await db.execute(select(func.coalesce(func.sum(Payment.amount), 0.0)).where(
        Payment.shift_id == shift.id, Payment.method == "cash", Payment.kind != "refund"))).scalar() or 0.0
    cash_refunds = (await db.execute(select(func.coalesce(func.sum(Payment.amount), 0.0)).where(
        Payment.shift_id == shift.id, Payment.method == "cash", Payment.kind == "refund"))).scalar() or 0.0
    pay_in = (await db.execute(select(func.coalesce(func.sum(CashDrawerMovement.amount), 0.0)).where(
        CashDrawerMovement.shift_id == shift.id, CashDrawerMovement.direction == "in"))).scalar() or 0.0
    pay_out = (await db.execute(select(func.coalesce(func.sum(CashDrawerMovement.amount), 0.0)).where(
        CashDrawerMovement.shift_id == shift.id, CashDrawerMovement.direction == "out"))).scalar() or 0.0
    # 144bh: manual cash income/expense booked on this location's till during the shift (signed:
    # income +, expense −). Linked at post time via Transaction.shift_id.
    from backend.models import Transaction
    manual_cash = (await db.execute(select(func.coalesce(func.sum(Transaction.amount), 0.0)).where(
        Transaction.shift_id == shift.id, Transaction.status == "active",
        Transaction.txn_type.in_(("income", "expense"))))).scalar() or 0.0
    expected = q2((shift.opening_float or 0.0) + cash_in - abs(cash_refunds) + pay_in - pay_out + manual_cash)
    return {"opening_float": q2(shift.opening_float or 0.0), "cash_in": q2(cash_in),
            "cash_refunds": q2(abs(cash_refunds)), "pay_in": q2(pay_in), "pay_out": q2(pay_out),
            "manual_cash": q2(manual_cash), "expected_cash": expected}


def _ser_shift(s: CashShift) -> dict:
    return {"id": s.id, "location_id": s.location_id, "opening_float": s.opening_float,
            "closing_float": s.closing_float, "expected_cash": s.expected_cash,
            "opened_at": s.opened_at.isoformat() if s.opened_at else None,
            "closed_at": s.closed_at.isoformat() if s.closed_at else None,
            "open": s.closed_at is None,
            "opened_by": (s.opened_by.name if getattr(s, "opened_by", None) else None),
            "discrepancy": (q2((s.closing_float or 0) - s.expected_cash) if s.closed_at and s.expected_cash is not None else None)}


@router.get("/reconciliation")
async def reconciliation(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    """144be — exception-based finance: scan the books for the money problems an owner would
    otherwise never catch (double-paid invoices, unbalanced drawers, supplier balances that have
    drifted from their ledger) and return them as a short 'needs your eyes' queue. The point is
    that the owner approves exceptions instead of reconciling by hand."""
    from backend.models import Supplier, Client, Account, ProcessorPayout, Transaction
    org = current.org_id
    items: list[dict] = []

    # 1) Invoices paid more than their total (double-click / double-post).
    dbl = (await db.execute(select(Invoice).where(
        Invoice.org_id == org, Invoice.status.notin_(("void", "cancelled", "canceled")),
        Invoice.amount_paid > Invoice.total + 0.01))).scalars().all()
    for inv in dbl:
        over = q2((inv.amount_paid or 0) - (inv.total or 0))
        items.append({"type": "double_paid", "severity": "high",
                      "title": f"Invoice {inv.invoice_number or inv.id[:8]} overpaid by {over}",
                      "detail": f"Paid {q2(inv.amount_paid or 0)} on a {q2(inv.total or 0)} invoice.",
                      "ref_type": "invoice", "ref_id": inv.id, "amount": over})

    # 2) Closed drawers that didn't balance.
    shifts = (await db.execute(select(CashShift).join(Location, CashShift.location_id == Location.id)
              .where(Location.org_id == org, CashShift.closed_at.isnot(None),
                     CashShift.expected_cash.isnot(None)).order_by(CashShift.closed_at.desc()).limit(100))).scalars().all()
    for s in shifts:
        d = q2((s.closing_float or 0) - s.expected_cash)
        if abs(d) >= 0.01:
            items.append({"type": "drawer_unbalanced", "severity": "high" if abs(d) >= 5 else "medium",
                          "title": f"Drawer {('over' if d > 0 else 'short')} {q2(abs(d))}",
                          "detail": f"Expected {q2(s.expected_cash)}, counted {q2(s.closing_float or 0)}.",
                          "ref_type": "shift", "ref_id": s.id, "amount": d})

    # 3) Supplier balance drifted from its linked contact ledger (the two should sum to ~0).
    sups = (await db.execute(select(Supplier).where(
        Supplier.org_id == org, Supplier.client_id.isnot(None)))).scalars().all()
    if sups:
        cids = {s.client_id for s in sups}
        cbal = dict((c.id, float(c.balance or 0)) for c in (await db.execute(
            select(Client).where(Client.id.in_(cids)))).scalars().all())
        for s in sups:
            drift = q2(float(s.balance or 0) + cbal.get(s.client_id, 0.0))
            if abs(drift) >= 0.01:
                items.append({"type": "supplier_drift", "severity": "medium",
                              "title": f"{s.name}: balance disagrees with ledger by {q2(abs(drift))}",
                              "detail": "The supplier's running balance and its ledger have drifted.",
                              "ref_type": "supplier", "ref_id": s.id, "amount": drift})

    # 4) Third leg — card/online deposits vs. what the processor actually paid out. Only checked
    #    once payouts exist (a connector or manual entry). Expected deposit = card/cashless
    #    invoice-payment transactions minus processing fees; compare to recorded payout net.
    payouts = (await db.execute(select(ProcessorPayout).where(ProcessorPayout.org_id == org))).scalars().all()
    if payouts:
        payouts_net = q2(sum(float(p.net_amount or 0) for p in payouts))
        card_in = (await db.execute(
            select(func.coalesce(func.sum(Transaction.amount), 0.0))
            .join(Account, Transaction.account_id == Account.id)
            .where(Transaction.org_id == org, Transaction.txn_type == "invoice_payment",
                   Transaction.status == "active", Account.payment_option.in_(("card", "cashless"))))).scalar() or 0.0
        fees = (await db.execute(
            select(func.coalesce(func.sum(func.abs(Transaction.amount)), 0.0)).where(
                Transaction.org_id == org, Transaction.txn_type == "fee", Transaction.status == "active"))).scalar() or 0.0
        expected = q2(float(card_in) - float(fees))
        gap = q2(expected - payouts_net)
        if abs(gap) >= 1.0:
            items.append({"type": "payout_gap", "severity": "high" if abs(gap) >= 20 else "medium",
                          "title": f"Card deposits don't match payouts by {q2(abs(gap))}",
                          "detail": f"Card payments net of fees ≈ {expected}, but recorded payouts total {payouts_net}.",
                          "ref_type": "payout", "ref_id": None, "amount": gap})

    order = {"high": 0, "medium": 1, "low": 2}
    items.sort(key=lambda x: order.get(x["severity"], 3))
    return {"items": items, "count": len(items)}


class PayoutIn(BaseModel):
    provider: str = "manual"
    payout_date: Optional[str] = None
    gross_amount: float
    fee_amount: float = 0.0
    net_amount: Optional[float] = None
    reference: Optional[str] = None
    note: Optional[str] = None


@router.post("/payouts", status_code=201)
async def record_payout(
    data: PayoutIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    """Record a processor deposit (manual for now; a Stripe/Square/SumUp connector will fill these
    into the same table later). net defaults to gross − fee."""
    from backend.models import ProcessorPayout
    net = data.net_amount if data.net_amount is not None else (data.gross_amount - data.fee_amount)
    p = ProcessorPayout(
        org_id=current.org_id, provider=data.provider or "manual",
        payout_date=date.fromisoformat(data.payout_date) if data.payout_date else date.today(),
        gross_amount=q2(data.gross_amount), fee_amount=q2(data.fee_amount), net_amount=q2(net),
        reference=data.reference, note=data.note, created_by_id=current.id)
    db.add(p)
    await db.commit()
    return {"ok": True, "id": p.id, "net_amount": p.net_amount}


@router.get("/payouts")
async def list_payouts(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    from backend.models import ProcessorPayout
    rows = (await db.execute(select(ProcessorPayout).where(ProcessorPayout.org_id == current.org_id)
            .order_by(ProcessorPayout.payout_date.desc(), ProcessorPayout.created_at.desc()).limit(limit))).scalars().all()
    return {"items": [{"id": p.id, "provider": p.provider,
                       "payout_date": p.payout_date.isoformat() if p.payout_date else None,
                       "gross_amount": p.gross_amount, "fee_amount": p.fee_amount, "net_amount": p.net_amount,
                       "reference": p.reference, "note": p.note} for p in rows]}


@router.post("/shifts/open")
async def open_shift(
    data: ShiftOpen,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_cashbox")),
):
    loc = (await db.execute(select(Location).where(
        Location.id == data.location_id, Location.org_id == current.org_id))).scalar_one_or_none()
    if not loc:
        raise HTTPException(404, "Location not found")
    # one open drawer per location — you can't open a second while one's live.
    existing = (await db.execute(select(CashShift).where(
        CashShift.location_id == data.location_id, CashShift.closed_at.is_(None)))).scalar_one_or_none()
    if existing:
        raise HTTPException(409, "A cash drawer is already open at this location — close it first.")
    opening = _denoms_total(data.denoms) if data.denoms else data.opening_float
    shift = CashShift(location_id=data.location_id, opened_by_id=current.id,
                      opening_float=opening, open_denoms=data.denoms)
    db.add(shift)
    await db.commit()
    await db.refresh(shift)
    return {"id": shift.id, "opened_at": shift.opened_at.isoformat(), "opening_float": shift.opening_float}


@router.get("/shifts/current")
async def current_shift(
    location_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """The open drawer at a location (or null). Lets the UI show open/closed state + take cash."""
    loc = (await db.execute(select(Location).where(
        Location.id == location_id, Location.org_id == current.org_id))).scalar_one_or_none()
    if not loc:
        raise HTTPException(404, "Location not found")
    shift = (await db.execute(select(CashShift).options(selectinload(CashShift.opened_by)).where(
        CashShift.location_id == location_id, CashShift.closed_at.is_(None)))).scalar_one_or_none()
    if not shift:
        return {"open": False}
    moves = (await db.execute(select(CashDrawerMovement).where(
        CashDrawerMovement.shift_id == shift.id).order_by(CashDrawerMovement.created_at))).scalars().all()
    out = _ser_shift(shift)
    out["movements"] = [{"id": m.id, "direction": m.direction, "amount": m.amount, "note": m.note,
                         "created_at": m.created_at.isoformat() if m.created_at else None} for m in moves]
    return out


@router.get("/shifts")
async def list_shifts(
    location_id: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    """Drawer history / reconciliation list — closed shifts with balanced/unbalanced status."""
    q = (select(CashShift).options(selectinload(CashShift.opened_by))
         .join(Location, CashShift.location_id == Location.id)
         .where(Location.org_id == current.org_id).order_by(CashShift.opened_at.desc()).limit(limit))
    if location_id:
        q = q.where(CashShift.location_id == location_id)
    rows = (await db.execute(q)).scalars().all()
    return {"items": [_ser_shift(s) for s in rows]}


@router.post("/shifts/{shift_id}/movement", status_code=201)
async def drawer_movement(
    shift_id: str, data: DrawerMoveIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_cashbox")),
):
    """Record a manual cash pay-in / pay-out on the open drawer (note required)."""
    shift = await _shift_or_404(db, current.org_id, shift_id)
    if shift.closed_at is not None:
        raise HTTPException(409, "This drawer is closed")
    if data.direction not in ("in", "out"):
        raise HTTPException(422, "direction must be in|out")
    amt = q2(data.amount)
    if amt <= 0:
        raise HTTPException(422, "Amount must be positive")
    if not (data.note or "").strip():
        raise HTTPException(422, "A note is required for every pay-in / pay-out")
    mv = CashDrawerMovement(org_id=current.org_id, shift_id=shift.id, direction=data.direction,
                            amount=amt, note=data.note.strip(), created_by_id=current.id)
    db.add(mv)
    await db.commit()
    return {"ok": True, "id": mv.id}


@router.post("/shifts/{shift_id}/close")
async def close_shift(
    shift_id: str,
    closing_float: Optional[float] = Query(None),   # back-compat; or send a body with denoms
    data: Optional[ShiftCloseIn] = Body(None),
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_cashbox")),
):
    shift = await _shift_or_404(db, current.org_id, shift_id)
    if shift.closed_at is not None:
        raise HTTPException(409, "This drawer is already closed")
    # counted total: from denominations if given, else the plain closing_float (query or body)
    denoms = data.denoms if data else None
    counted = _denoms_total(denoms) if denoms else (
        (data.closing_float if data and data.closing_float is not None else closing_float) or 0.0)
    counted = q2(counted)

    parts = await _expected_cash(db, shift)
    shift.expected_cash = parts["expected_cash"]
    shift.closing_float = counted
    shift.close_denoms = denoms
    shift.closed_at = datetime.utcnow()
    await db.commit()

    discrepancy = q2(counted - shift.expected_cash)
    # 144bb blind count: only a finance-permitted user is shown expected + discrepancy.
    show_expected = True
    if current.role_id:
        from backend.models import Role
        from backend.services.permissions import role_allows
        role = (await db.execute(select(Role).where(Role.id == current.role_id))).scalar_one_or_none()
        show_expected = role_allows(role, "can_view_finance")
    resp = {"ok": True, "counted": counted}
    if show_expected:
        resp["expected_cash"] = shift.expected_cash
        resp["discrepancy"] = discrepancy
        resp["breakdown"] = parts
        # 144bc — explain a non-trivial variance instead of just stating it.
        if abs(discrepancy) >= 0.01:
            moves = (await db.execute(select(CashDrawerMovement).where(
                CashDrawerMovement.shift_id == shift.id))).scalars().all()
            causes = []
            if moves:
                causes.append(f"{len(moves)} manual pay-in/out(s) this shift (pay-in {parts['pay_in']}, "
                              f"pay-out {parts['pay_out']}) — check their notes")
            causes.append("over" if discrepancy > 0 else "short")
            resp["explanation"] = {
                "direction": "over" if discrepancy > 0 else "short",
                "amount": abs(discrepancy),
                "checklist": causes,
                "movements": [{"direction": m.direction, "amount": m.amount, "note": m.note} for m in moves],
            }
    return resp

@router.post("/expenses", status_code=201)
async def add_expense(
    data: ExpenseIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_create_expenses")),
):
    expense = Expense(
        org_id=current.org_id,
        location_id=data.location_id,
        category=data.category,
        recorded_by_id=current.id,
        description=data.description,
        amount=data.amount,
        expense_date=date.fromisoformat(data.expense_date) if data.expense_date else date.today(),
    )
    db.add(expense)
    await db.commit()
    return {"ok": True}

@router.get("/pnl")
async def profit_and_loss(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    location_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Owner P&L: net revenue − parts COGS − operating expenses, org-scoped."""
    from backend.services.finance_pnl import compute_pnl
    return await compute_pnl(db, current.org_id, date_from, date_to, location_id)


@router.get("/reports/summary")
async def finance_summary(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    total_revenue = (await db.execute(
        select(func.sum(Invoice.total)).where(Invoice.org_id == current.org_id, Invoice.status == "paid")
    )).scalar() or 0
    total_outstanding = (await db.execute(
        select(func.sum(Invoice.total - Invoice.amount_paid))
        .where(Invoice.org_id == current.org_id, Invoice.status.in_(["draft", "partial"]))
    )).scalar() or 0
    total_expenses = (await db.execute(
        select(func.sum(Expense.amount)).where(Expense.org_id == current.org_id)
    )).scalar() or 0
    invoice_count = (await db.execute(
        select(func.count()).select_from(Invoice).where(Invoice.org_id == current.org_id)
    )).scalar() or 0
    return {
        "total_revenue": q2(total_revenue),
        "total_outstanding": q2(total_outstanding),
        "total_expenses": q2(total_expenses),
        "gross_profit": q2(total_revenue - total_expenses),
        "invoice_count": invoice_count,
    }


# ── 144bk: accountant export ──────────────────────────────────────────
@router.get("/export/accountant.csv")
async def accountant_export(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_export_accountant")),
):
    """A clean CSV of the period's cash transactions (active only), so the owner hands the CPA one
    file instead of re-keying into QuickBooks. Default period = current month to today."""
    import csv, io
    from backend.models import Transaction, Account, CashFlowItem
    today = date.today()
    df = datetime.fromisoformat(date_from) if date_from else datetime(today.year, today.month, 1)
    if date_to:
        _dt = datetime.fromisoformat(date_to)
        dt = _dt.replace(hour=23, minute=59, second=59) if _dt.time() == datetime.min.time() else _dt
    else:
        dt = datetime(today.year, today.month, today.day, 23, 59, 59)
    rows = (await db.execute(
        select(Transaction, Account.name, CashFlowItem.name)
        .join(Account, Transaction.account_id == Account.id)
        .outerjoin(CashFlowItem, Transaction.cash_flow_item_id == CashFlowItem.id)
        .where(Transaction.org_id == current.org_id, Transaction.status == "active",
               Transaction.actual_date >= df, Transaction.actual_date <= dt)
        .order_by(Transaction.actual_date))).all()
    # Combine cashbox transactions with business expenses (Expense model — rent/utilities that never
    # hit a cashbox but belong in the accountant pack), sorted by date.
    lines: list[tuple] = []
    for txn, acc_name, cat_name in rows:
        amt = float(txn.amount or 0)
        d0 = txn.actual_date
        lines.append((d0, [d0.strftime("%Y-%m-%d %H:%M") if d0 else "", acc_name or "", txn.txn_type or "",
                           cat_name or "", txn.comment or "", f"{amt:.2f}" if amt > 0 else "",
                           f"{abs(amt):.2f}" if amt < 0 else "",
                           f"{float(txn.balance_after):.2f}" if txn.balance_after is not None else ""]))
    exps = (await db.execute(select(Expense).where(
        Expense.org_id == current.org_id, Expense.expense_date >= df.date(), Expense.expense_date <= dt.date()))).scalars().all()
    for e in exps:
        ed = datetime.combine(e.expense_date, datetime.min.time()) if e.expense_date else None
        lines.append((ed, [e.expense_date.strftime("%Y-%m-%d") if e.expense_date else "", "(business expense)",
                           "expense", e.category or "", e.description or "", "", f"{float(e.amount or 0):.2f}", ""]))
    lines.sort(key=lambda t: (t[0] is None, t[0]))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Date", "Account", "Type", "Category", "Comment", "Income", "Expense", "Balance after"])
    for _d, cells in lines:
        w.writerow(cells)
    buf.seek(0)
    from fastapi.responses import StreamingResponse
    fname = f"accountant_{df.strftime('%Y%m%d')}_{dt.strftime('%Y%m%d')}.csv"
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{fname}"'})
