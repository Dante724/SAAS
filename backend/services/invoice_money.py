"""Invoice money — the one set of rules every payment and invoice path follows.

Before this, about six code paths created invoices and four posted payments, each its own way:
a deposit never reached the invoice (G4), a job payment piled onto the newest invoice capped at
the job balance (F2), an invoice for several jobs credited only the first (F3), invoice totals
were stored once and never followed the job (F4), and tax/discount meant a percentage on manual
invoices and an amount everywhere else (F5). See docs/audits/2026-10-30-estimate-to-invoice-flow.md.

The rules:
- Every invoice is made by new_invoice(): it links every job it bills (InvoiceOrder), computes
  its subtotal from what it bills, and picks up money already taken on those jobs.
- Invoice discount and tax are amounts: total = subtotal − discount + tax.
- Money for a job (till, card reader, pay link) goes through apply_order_payment(): each open
  invoice of the job takes at most its own balance, oldest first. Anything left is held on the
  job (a Payment with no invoice) and lands on the next invoice the job gets.
- Money for an invoice goes through apply_payment_to_invoice(): one Payment per job it bills,
  each up to that job's balance, so every job sees its share.
- A draft or part-paid invoice that hasn't been sent follows its jobs' lines. Once sent or paid
  it is locked: it keeps the lines it billed, those lines can't be repriced, and later work is
  owed on the job and billed on a new invoice.

Callers commit; notify_payment_received() runs after the commit.
"""
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Invoice, InvoiceLine, InvoiceOrder, Order, OrderLine, Payment
from backend.services.invoicing import VOID_STATUSES, balance_due, invoice_orders, settle_status
from backend.services.money import q2

MONEY_IN = ("payment", "prepayment")
_COPY = ("client_id", "method", "kind", "order_id", "account_id", "received_by_id", "shift_id",
         "reference", "provider_ref", "paid_at")


def is_locked(inv: Invoice) -> bool:
    """Sent to the customer, paid in full, or issued as an e-invoice: it no longer changes."""
    return ((inv.status or "") in ("paid", "issued", "overdue") or inv.sent_at is not None
            or (inv.einvoice_status or "none") in ("issued", "cleared"))


def retotal(inv: Invoice) -> None:
    inv.total = q2(float(inv.subtotal or 0) - float(inv.discount or 0) + float(inv.tax or 0))


def pct_amounts(subtotal: float, discount_pct: float, tax_pct: float) -> tuple[float, float]:
    """Percentages → amounts: the discount comes off first, tax is on what's left."""
    discount = q2(float(subtotal) * float(discount_pct or 0) / 100)
    return discount, q2((float(subtotal) - discount) * float(tax_pct or 0) / 100)


async def lock_order(db: AsyncSession, order_id: str) -> None:
    """Serialize money moves on one job (Postgres row lock; SQLite already serializes writes), so
    two requests can't both apply the same held deposit or both create the job's first invoice."""
    await db.execute(select(Order.id).where(Order.id == order_id).with_for_update())


async def invoices_for_order(db: AsyncSession, order_id: str) -> list[Invoice]:
    """Every live (not cancelled) invoice billing the job, as primary or linked, oldest first."""
    linked = select(InvoiceOrder.invoice_id).where(InvoiceOrder.order_id == order_id)
    return list((await db.execute(select(Invoice).where(
        (Invoice.order_id == order_id) | Invoice.id.in_(linked),
        Invoice.status.notin_(VOID_STATUSES)).order_by(Invoice.created_at, Invoice.invoice_number)
    )).scalars().all())


async def _billed_rows(db: AsyncSession, inv: Invoice) -> list[InvoiceLine]:
    return list((await db.execute(select(InvoiceLine).where(
        InvoiceLine.invoice_id == inv.id, InvoiceLine.order_line_id.isnot(None)))).scalars().all())


async def source_subtotal(db: AsyncSession, inv: Invoice) -> float:
    """What the invoice bills right now: the job lines it lists (or the whole job when it lists
    none of that job's lines) plus its own free lines."""
    billed = {r.order_line_id for r in await _billed_rows(db, inv)}
    total = 0.0
    for o in await invoice_orders(db, inv):
        lines = (await db.execute(select(OrderLine).where(OrderLine.order_id == o.id))).scalars().all()
        mine = [l for l in lines if l.id in billed]
        total += sum(float(l.total or 0) for l in mine) if mine else float(o.total or 0)
    free = (await db.execute(select(InvoiceLine.total).where(
        InvoiceLine.invoice_id == inv.id, InvoiceLine.order_line_id.is_(None)))).scalars().all()
    return q2(total + sum(float(t or 0) for t in free))


async def new_invoice(db: AsyncSession, *, org_id: str, orders: list[Order] = (), client_id: str | None = None,
                      lines: list[OrderLine] = (), free_lines: list[dict] = (), subtotal: float | None = None,
                      discount: float = 0.0, tax: float = 0.0, discount_pct: float | None = None,
                      tax_pct: float | None = None, display_mode: str = "detailed",
                      issued_at: datetime | None = None, **extra) -> Invoice:
    """The one way an invoice is made. ``lines`` bills just those job lines (one job); otherwise
    each job in ``orders`` is billed whole. ``subtotal`` overrides the computed one (manual
    invoices); percentages, when given, are turned into amounts on the final subtotal."""
    from backend.services.numbering import next_number
    orders = list(orders)
    for o in orders:
        await lock_order(db, o.id)
    inv = Invoice(invoice_number=await next_number(db, org_id, "invoice"), org_id=org_id,
                  order_id=orders[0].id if orders else None,
                  client_id=client_id or (orders[0].client_id if orders else None),
                  display_mode=display_mode or "detailed", status="draft", subtotal=0.0,
                  discount=0.0, tax=0.0, total=0.0, amount_paid=0.0,
                  issued_at=issued_at or datetime.utcnow(), **extra)
    db.add(inv)
    await db.flush()
    for i, o in enumerate(orders):
        db.add(InvoiceOrder(invoice_id=inv.id, order_id=o.id, sort_order=i))
    for i, l in enumerate(lines):
        db.add(InvoiceLine(invoice_id=inv.id, order_line_id=l.id, kind=(l.kind or l.line_type or "custom")[:16],
                           sort_order=i, description=(l.description or "")[:500],
                           qty=l.qty, unit_price=l.unit_price, total=l.total))
    for i, l in enumerate(free_lines):
        qty, price = float(l.get("qty") or 0), float(l.get("unit_price") or 0)
        db.add(InvoiceLine(invoice_id=inv.id, order_line_id=None, kind=(l.get("kind") or "custom")[:16],
                           source_id=l.get("source_id"), sort_order=i,
                           description=(l.get("description") or "")[:500], qty=qty, unit_price=price,
                           total=q2(qty * price)))
    await db.flush()
    inv.subtotal = q2(subtotal) if subtotal else await source_subtotal(db, inv)
    if discount_pct is not None or tax_pct is not None:
        discount, tax = pct_amounts(inv.subtotal, discount_pct or 0, tax_pct or 0)
    inv.discount, inv.tax = q2(discount or 0), q2(tax or 0)
    retotal(inv)
    await db.flush()
    for o in orders:
        await apply_order_money(db, o)    # G4: a deposit taken before the invoice existed
    return inv


async def room_for_job(db: AsyncSession, inv: Invoice, order_id: str) -> float:
    """How much of this invoice's balance one job's money may fill. On a one-job invoice, all of
    it; on a shared invoice, only that job's own share (its billed lines, or its total) less what
    the job has already paid on it — so B's deposit never pays A's part."""
    bal = balance_due(inv)
    orders = await invoice_orders(db, inv)
    if len(orders) <= 1:
        return bal
    billed = {r.order_line_id for r in await _billed_rows(db, inv)}
    lines = (await db.execute(select(OrderLine).where(OrderLine.order_id == order_id))).scalars().all()
    mine = [l for l in lines if l.id in billed]
    order = next((o for o in orders if o.id == order_id), None)
    share = sum(float(l.total or 0) for l in mine) if mine else float(order.total or 0) if order else 0.0
    rows = (await db.execute(select(Payment.kind, Payment.amount).where(
        Payment.invoice_id == inv.id, Payment.order_id == order_id))).all()
    paid = sum(float(a or 0) * (-1 if k == "refund" else 1) for k, a in rows)
    return q2(max(0.0, min(bal, share - paid)))


def _new_payment(**kw) -> Payment:
    return Payment(**{k: v for k, v in kw.items() if v is not None or k in ("invoice_id", "order_id")})


async def _credit(db: AsyncSession, inv: Invoice, amount: float) -> None:
    inv.amount_paid = q2(float(inv.amount_paid or 0) + amount)
    settle_status(inv)
    if inv.status == "paid":
        await freeze_lines(db, inv)


async def apply_order_money(db: AsyncSession, order: Order) -> float:
    """Put money held on the job (deposits, payments taken before it had an invoice for them) on
    its open invoices, oldest first, each up to its balance. Refunds not tied to an invoice come
    off what's held first, so money already handed back is never applied. Returns the amount
    applied. A held payment bigger than what's needed is split; the rest stays held."""
    await lock_order(db, order.id)
    rows = list((await db.execute(select(Payment).where(
        Payment.order_id == order.id, Payment.invoice_id.is_(None), Payment.kind.in_(MONEY_IN))
        .order_by(Payment.paid_at, Payment.id).with_for_update())).scalars().all())
    if not rows:
        return 0.0
    refunds = [float(x or 0) for x in (await db.execute(select(Payment.amount).where(
        Payment.order_id == order.id, Payment.invoice_id.is_(None), Payment.kind == "refund"))).scalars().all()]
    pool = q2(sum(float(r.amount or 0) for r in rows) - sum(abs(x) for x in refunds))
    applied = 0.0
    for inv in await invoices_for_order(db, order.id):
        need = q2(min(await room_for_job(db, inv, order.id), pool - applied))
        put = 0.0
        while need > 0 and rows:
            r = rows[0]
            take = q2(min(float(r.amount or 0), need))
            if take < q2(float(r.amount or 0)):
                rest = Payment(**{k: getattr(r, k) for k in _COPY}, invoice_id=None,
                               amount=q2(float(r.amount) - take))
                db.add(rest)
                r.amount = take
                rows[0] = rest
            else:
                rows.pop(0)
            r.invoice_id = inv.id
            need, put = q2(need - take), q2(put + take)
        if put:
            applied = q2(applied + put)
            await _credit(db, inv, put)
        if not rows or applied >= pool:
            break
    await db.flush()
    return applied


async def apply_order_payment(db: AsyncSession, order: Order, amount: float, **fields) -> list[Payment]:
    """Money in for a job (F2). Each open invoice of the job takes at most its own balance,
    oldest first; a job with no invoice gets one. What's left is held on the job and lands on the
    next invoice it gets. ``fields`` are the Payment columns (method, account_id, …). Returns the
    Payment rows, the first on the oldest invoice it reached."""
    await lock_order(db, order.id)
    invs = await invoices_for_order(db, order.id)
    if not invs:
        invs = [await new_invoice(db, org_id=order.org_id, orders=[order])]
    fields.setdefault("client_id", order.client_id)
    fields.setdefault("kind", "payment")
    left, out = q2(amount), []
    for inv in invs:
        take = q2(min(left, await room_for_job(db, inv, order.id)))
        if take <= 0:
            continue
        out.append(_new_payment(invoice_id=inv.id, order_id=order.id, amount=take, **fields))
        db.add(out[-1])
        await _credit(db, inv, take)
        left = q2(left - take)
        if left <= 0:
            break
    if left > 0:
        out.append(_new_payment(invoice_id=None, order_id=order.id, amount=left, **fields))
        db.add(out[-1])
    await db.flush()
    return out


async def apply_payment_to_invoice(db: AsyncSession, inv: Invoice, amount: float, **fields) -> list[Payment]:
    """Money in against an invoice (F3). One Payment per job the invoice bills, each up to that
    job's balance in the invoice's job order, so every job sees its share; anything over (the
    invoice's own free lines) is kept on the invoice without a job. A one-job invoice puts it
    all on its job. The caller has already capped ``amount`` at the invoice balance."""
    from backend.services.order_money import balance_due as job_due
    orders = await invoice_orders(db, inv)
    fields.setdefault("client_id", inv.client_id)
    fields.setdefault("kind", "payment")
    left, out = q2(amount), []
    if len(orders) <= 1:
        parts = [(orders[0].id if orders else None, left)]
    else:
        parts = []
        for o in orders:
            take = q2(min(left, await job_due(db, o)))
            if take > 0:
                parts.append((o.id, take))
                left = q2(left - take)
        if left > 0:
            parts.append((None, left))
    for oid, amt in parts:
        out.append(_new_payment(invoice_id=inv.id, order_id=oid, amount=amt, **fields))
        db.add(out[-1])
    await _credit(db, inv, q2(amount))
    await db.flush()
    return out


async def freeze_lines(db: AsyncSession, inv: Invoice) -> None:
    """Locking an invoice (sent or paid) records the job lines it bills, so lines added to the
    job later don't appear on it and are billed separately instead."""
    have = {r.order_line_id for r in await _billed_rows(db, inv)}
    n = len(have)
    for o in await invoice_orders(db, inv):
        lines = (await db.execute(select(OrderLine).where(OrderLine.order_id == o.id)
                                  .order_by(OrderLine.id))).scalars().all()
        if any(l.id in have for l in lines):
            continue                     # it already lists exactly the lines it bills for this job
        for l in lines:
            db.add(InvoiceLine(invoice_id=inv.id, order_line_id=l.id, kind=(l.kind or l.line_type or "custom")[:16],
                               sort_order=n, description=(l.description or "")[:500],
                               qty=l.qty, unit_price=l.unit_price, total=l.total))
            n += 1
    await db.flush()


async def release_payments(db: AsyncSession, inv: Invoice, amount: float, *, prefer_order: str | None = None) -> float:
    """Take up to ``amount`` of job money off an invoice and hold it on its job again (newest
    first, the preferred job's first; a row is split when only part comes off). Money without a
    job (paid against free lines) stays. Returns what came off."""
    rows = list((await db.execute(select(Payment).where(
        Payment.invoice_id == inv.id, Payment.kind.in_(MONEY_IN), Payment.order_id.isnot(None))
        .order_by(Payment.paid_at.desc(), Payment.id.desc()).with_for_update())).scalars().all())
    rows.sort(key=lambda r: r.order_id != prefer_order)
    left = q2(amount)
    for r in rows:
        if left <= 0:
            break
        take = q2(min(float(r.amount or 0), left))
        if take < q2(float(r.amount or 0)):
            db.add(Payment(**{k: getattr(r, k) for k in _COPY}, invoice_id=None, amount=take))
            r.amount = q2(float(r.amount) - take)
        else:
            r.invoice_id = None
        left = q2(left - take)
    moved = q2(amount - left)
    inv.amount_paid = q2(max(float(inv.amount_paid or 0) - moved, 0))
    settle_status(inv)
    await db.flush()
    return moved


async def mark_sent(db: AsyncSession, inv: Invoice) -> None:
    """The customer has it now: draft → issued, and it stops following the job."""
    inv.sent_at = inv.sent_at or datetime.utcnow()
    if (inv.status or "draft") == "draft":
        inv.status = "issued"
    await freeze_lines(db, inv)


async def locking_invoice(db: AsyncSession, line_id: str) -> Invoice | None:
    return next((inv for inv in (await db.execute(select(Invoice).join(
        InvoiceLine, InvoiceLine.invoice_id == Invoice.id).where(
        InvoiceLine.order_line_id == line_id, Invoice.status.notin_(VOID_STATUSES)))).scalars().all()
        if is_locked(inv)), None)


async def assert_line_editable(db: AsyncSession, line_id: str) -> None:
    """A job line on a sent or paid invoice can't be changed under it."""
    inv = await locking_invoice(db, line_id)
    if inv:
        raise HTTPException(409, f"This line is on invoice {inv.invoice_number}, which has been sent or paid, "
                                 "so it can't be changed. Add a new line for extra work, or issue a credit note.")


async def release_line(db: AsyncSession, line_id: str) -> None:
    """Before a job line is deleted: refused when a sent or paid invoice bills it; otherwise it is
    taken off the draft invoices that list it (they're recomputed with the job)."""
    await assert_line_editable(db, line_id)
    for r in (await db.execute(select(InvoiceLine).where(InvoiceLine.order_line_id == line_id))).scalars().all():
        await db.delete(r)
    await db.flush()


async def sync_order_invoices(db: AsyncSession, order: Order, prev_total: float) -> None:
    """The job's lines changed (F4). Unlocked invoices follow: one listing job lines is
    recomputed from them, one billing the whole job moves by the change in the job total (so a
    hand-set amount is kept). A locked invoice keeps its total; the job's history says the
    difference is still owed on the job. Refused when a draft would drop below what's been
    paid on it. Money held on the job then lands on what grew."""
    from backend.routers.feed import log_system_event
    delta = q2(float(order.total or 0) - float(prev_total or 0))
    for inv in await invoices_for_order(db, order.id):
        if is_locked(inv):
            if delta:
                await log_system_event(db, order.org_id, "order", order.id,
                                       f"Job total changed by {delta:+.2f} after invoice {inv.invoice_number} was "
                                       "sent or paid. That invoice keeps its total; the difference is owed on the "
                                       "job and goes on a new invoice.")
            continue
        sub = await source_subtotal(db, inv) if await _billed_rows(db, inv) else q2(float(inv.subtotal or 0) + delta)
        if sub == q2(float(inv.subtotal or 0)):
            continue
        new_total = q2(sub - float(inv.discount or 0) + float(inv.tax or 0))
        excess = q2(float(inv.amount_paid or 0) + float(inv.credited or 0) - new_total)
        if excess > float(inv.amount_paid or 0):     # checked before anything changes
            raise HTTPException(409, f"Invoice {inv.invoice_number} has credit notes worth more than its new total "
                                     f"of {new_total:.2f}. Cancel a credit note first.")
        if excess > 0:
            # Paid more than it now bills: the extra goes back to being held on the job, where it
            # shows as an overpayment staff can refund (or it pays the job's next invoice).
            await release_payments(db, inv, excess, prefer_order=order.id)
        inv.subtotal = sub
        retotal(inv)
        settle_status(inv)
        if inv.status == "paid":
            await freeze_lines(db, inv)
    await db.flush()
    await apply_order_money(db, order)


async def attach_order(db: AsyncSession, inv: Invoice, order: Order) -> None:
    """Bill one more job on an invoice: link it, add its total, apply its held money."""
    n = len(await invoice_orders(db, inv))
    db.add(InvoiceOrder(invoice_id=inv.id, order_id=order.id, sort_order=n))
    if not inv.order_id:
        inv.order_id = order.id
    inv.subtotal = q2(float(inv.subtotal or 0) + float(order.total or 0))
    retotal(inv)
    await db.flush()
    await apply_order_money(db, order)


async def detach_order(db: AsyncSession, inv: Invoice, order_id: str) -> bool:
    """Stop billing a job on an invoice. Refused while that job's money is on it."""
    link = (await db.execute(select(InvoiceOrder).where(
        InvoiceOrder.invoice_id == inv.id, InvoiceOrder.order_id == order_id))).scalar_one_or_none()
    if not link and inv.order_id != order_id:
        return False
    paid = (await db.execute(select(Payment.id).where(
        Payment.invoice_id == inv.id, Payment.order_id == order_id).limit(1))).scalar_one_or_none()
    if paid:
        raise HTTPException(409, "This job has payments on this invoice. Refund them first, then remove the job.")
    order = (await db.execute(select(Order).where(Order.id == order_id))).scalar_one_or_none()
    billed = [r for r in await _billed_rows(db, inv)
              if r.order_line_id in set((await db.execute(select(OrderLine.id).where(
                  OrderLine.order_id == order_id))).scalars().all())]
    amount = sum(float(r.total or 0) for r in billed) if billed else float(order.total or 0) if order else 0.0
    for r in billed:
        await db.delete(r)
    if link:
        await db.delete(link)
    if inv.order_id == order_id:
        nxt = (await db.execute(select(InvoiceOrder).where(
            InvoiceOrder.invoice_id == inv.id, InvoiceOrder.order_id != order_id)
            .order_by(InvoiceOrder.sort_order))).scalars().first()
        inv.order_id = nxt.order_id if nxt else None
    inv.subtotal = q2(max(float(inv.subtotal or 0) - amount, 0))
    retotal(inv)
    settle_status(inv)
    await db.flush()
    return True


async def reverse_charge(db: AsyncSession, order: Order, charge_ref: str | None, first_payment_id: str | None,
                         amount: float, already: float, **fields) -> list[Payment]:
    """A card charge given back (in part). The charge may have been split across invoices and
    money held on the job; the refund comes off those same rows, held money first, then the
    newest invoice, skipping what earlier refunds of this charge already took. One refund
    Payment per invoice (or none = held money), each invoice's paid amount lowered to match."""
    cond = Payment.provider_ref == charge_ref if charge_ref else Payment.id == first_payment_id
    rows = list((await db.execute(select(Payment).where(
        Payment.order_id == order.id, Payment.kind == "payment", cond)
        .order_by(Payment.paid_at, Payment.id))).scalars().all())
    held = [r for r in rows if r.invoice_id is None]
    rows = held + list(reversed([r for r in rows if r.invoice_id is not None]))   # held first, newest invoice next
    skip, left, out = q2(already), q2(amount), []
    by_inv: dict = {}
    for r in rows:
        room = q2(float(r.amount or 0))
        used = min(room, skip)
        skip, room = q2(skip - used), q2(room - used)
        take = q2(min(room, left))
        if take > 0:
            by_inv[r.invoice_id] = q2(by_inv.get(r.invoice_id, 0) + take)
            left = q2(left - take)
    if left > 0:
        by_inv[None] = q2(by_inv.get(None, 0) + left)
    for iid, amt in by_inv.items():
        out.append(_new_payment(order_id=order.id, invoice_id=iid, client_id=order.client_id, kind="refund",
                                amount=amt, **fields))
        db.add(out[-1])
        if iid:
            inv = (await db.execute(select(Invoice).where(Invoice.id == iid))).scalar_one_or_none()
            if inv:
                inv.amount_paid = q2(max(float(inv.amount_paid or 0) - amt, 0))
                settle_status(inv)
    await db.flush()
    return out


async def notify_payment_received(db: AsyncSession, org_id: str, order_ids, actor_id: str | None = None) -> None:
    """payment_received rules (customer receipt + staff), for every job the money reached.
    Best effort, after the commit."""
    from backend.services import notification_rules as _nr
    for oid in dict.fromkeys(o for o in order_ids if o):
        try:
            await _nr.evaluate(db, org_id=org_id, order_id=oid, event="payment_received")
            await _nr.notify_staff_order(db, org_id=org_id, order_id=oid, event="payment_received",
                                         actor_id=actor_id)
        except Exception:  # noqa: BLE001 — notifications never undo a recorded payment
            import logging
            logging.getLogger("tekly.payments").exception("payment_received notification failed for %s", oid)
