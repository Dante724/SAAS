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
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Invoice, InvoiceLine, InvoiceOrder, Order, OrderLine, Payment
from backend.services.invoicing import VOID_STATUSES, balance_due, invoice_orders, settle_status
from backend.services.money import add_to_column, q2

MONEY_IN = ("payment", "prepayment")
_COPY = ("org_id", "client_id", "method", "kind", "order_id", "account_id", "received_by_id", "shift_id",
         "reference", "provider_ref", "paid_at")


def is_locked(inv: Invoice) -> bool:
    """Sent to the customer, paid in full, or issued as an e-invoice: it no longer changes."""
    return ((inv.status or "") in ("paid", "issued", "overdue") or inv.sent_at is not None
            or (inv.einvoice_status or "none") in ("issued", "cleared"))


def retotal(inv: Invoice) -> None:
    # never below zero (audit H5): a negative total reads as an overpayment that could be refunded
    inv.total = max(q2(float(inv.subtotal or 0) - float(inv.discount or 0) + float(inv.tax or 0)), 0.0)


def pct_amounts(subtotal: float, discount_pct: float, tax_pct: float) -> tuple[float, float]:
    """Percentages → amounts: the discount comes off first, tax is on what's left."""
    discount = q2(float(subtotal) * float(discount_pct or 0) / 100)
    return discount, q2((float(subtotal) - discount) * float(tax_pct or 0) / 100)


async def lock_order(db: AsyncSession, order_id: str) -> None:
    """Serialize changes to one job's money and lines, so two requests can't both apply the same
    held deposit, both create the job's first invoice, or edit its lines over each other. Postgres:
    a row lock (FOR NO KEY UPDATE). SQLite ignores FOR UPDATE and only serializes writes, so there a no-op UPDATE
    takes the write lock now (the second request waits, then reads what the first one saved)."""
    if db.get_bind().dialect.name == "sqlite":
        from sqlalchemy import update
        await db.execute(update(Order).where(Order.id == order_id)
                         .values(updated_at=Order.updated_at).execution_options(synchronize_session=False))
    else:
        # FOR NO KEY UPDATE: invoices, payments and lines reference the job by foreign key, so
        # writing one takes FOR KEY SHARE on the job; a plain FOR UPDATE held for another job on
        # the same invoice deadlocked. NO KEY UPDATE still serialises two writers of one job.
        await db.execute(select(Order.id).where(Order.id == order_id).with_for_update(key_share=True))


async def billed_on(db: AsyncSession, order_id: str, *, exclude_invoice_id: str | None = None):
    """What live invoices already bill on this job, read under lock_order: (an invoice billing
    the whole job — it lists none of the job's lines, so it follows the job — or None,
    {order_line_id: invoice} for lines listed on an invoice, partial or frozen when sent/paid)."""
    job_lines = set((await db.execute(select(OrderLine.id).where(OrderLine.order_id == order_id))).scalars().all())
    whole, lines = None, {}
    for inv in await invoices_for_order(db, order_id):
        if inv.id == exclude_invoice_id:
            continue
        mine = [r.order_line_id for r in await _billed_rows(db, inv) if r.order_line_id in job_lines]
        if mine:
            lines.update({lid: inv for lid in mine})
        elif whole is None:
            whole = inv
    return whole, lines


async def invoices_for_order(db: AsyncSession, order_id: str) -> list[Invoice]:
    """Every live (not cancelled) invoice billing the job, as primary or linked, oldest first."""
    linked = select(InvoiceOrder.invoice_id).where(InvoiceOrder.order_id == order_id)
    return list((await db.execute(select(Invoice).where(
        (Invoice.order_id == order_id) | Invoice.id.in_(linked),
        Invoice.status.notin_(VOID_STATUSES)).order_by(Invoice.created_at, Invoice.invoice_number)
    )).scalars().all())


async def lock_invoices_of(db: AsyncSession, order_id: str) -> None:
    """Lock the job's live invoices (ids sorted) right after the job's own lock and BEFORE the
    cashbox and client ledger postings: Order -> Invoice -> Client, the order every money-in
    path follows (M22). A prepayment or a settling pay link that took the till and the client's
    row first and then wrote the shared invoice deadlocked against Invoices › Record payment,
    which holds that invoice while it posts to the same till and client. No-op without
    invoices; FOR UPDATE is a no-op on SQLite."""
    ids = sorted(i.id for i in await invoices_for_order(db, order_id))
    if ids:
        await db.execute(select(Invoice.id).where(Invoice.id.in_(ids)).order_by(Invoice.id).with_for_update())


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


def _jobs_tax(orders) -> float:
    """The tax the jobs already carry (VAT inside their prices, or sales tax added on top)."""
    return q2(sum(float(getattr(o, "tax", 0) or 0) for o in orders))


def _lines_tax(lines) -> float:
    """The tax a subset of job lines carries (M11): a per-line or "bill the rest" invoice used
    to take none of it, so a taxed job billed in parts exported as zero-rated."""
    from backend.services.tax import line_tax
    return q2(sum(line_tax(qty=l.qty, unit_price=l.unit_price, discount=l.discount,
                           discount_type=l.discount_type, tax_rate=l.tax_rate, tax_mode=l.tax_mode)
                  for l in lines))


async def _has_free_lines(db: AsyncSession, inv: Invoice) -> bool:
    return bool((await db.execute(select(InvoiceLine.id).where(
        InvoiceLine.invoice_id == inv.id, InvoiceLine.order_line_id.is_(None)).limit(1))).scalar_one_or_none())


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
    elif orders and not free_lines and not subtotal and not tax:
        # Whole jobs (or a subset of their lines): the totals already include their tax, so it's
        # shown as the invoice's tax and the subtotal is what's left. The total doesn't change.
        tax = _lines_tax(lines) if lines else _jobs_tax(orders)
        inv.subtotal = q2(inv.subtotal - tax)
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
    # Atomic: apply_order_payment locks the job, not the invoice, so two jobs of one invoice
    # paid together both read the same amount_paid and one payment was lost (100 instead of 160).
    # The status is settled from the value the UPDATE left behind.
    await add_to_column(db, inv, "amount_paid", amount)
    settle_status(inv)
    if inv.status == "paid":
        await freeze_lines(db, inv)


async def _sync_plan_periods(db: AsyncSession, *, invoice_ids=(), order_ids=()) -> None:
    """QA-5: a membership period billed on this invoice / job is marked paid with the payment
    (the memberships list used to do it on every GET, for the whole shop, and commit)."""
    from backend.services.memberships import sync_paid_by
    await sync_paid_by(db, invoice_ids=invoice_ids, order_ids=order_ids)


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
    if applied:
        await _sync_plan_periods(db, invoice_ids=[i.id for i in await invoices_for_order(db, order.id)],
                                 order_ids=[order.id])
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
    from backend.services.order_money import resync_write_off   # paid after dropping: less is written off
    await resync_write_off(db, order, fields.get("received_by_id"))
    await _sync_plan_periods(db, invoice_ids=[p.invoice_id for p in out], order_ids=[order.id])
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
    from backend.services.order_money import resync_write_off   # paid after dropping: less is written off
    for o in orders:
        await resync_write_off(db, o, fields.get("received_by_id"))
    await _sync_plan_periods(db, invoice_ids=[inv.id], order_ids=[o.id for o in orders])
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


async def _take_off_paid(db: AsyncSession, inv: Invoice, amount: float) -> None:
    """Lower an invoice's paid amount by `amount`, capped at what it holds. Re-review inventory N6:
    the cap was read from the in-memory row, which a refund on another job sharing this invoice may
    have lowered meanwhile (callers lock the job, not the invoice), so the decrement went below zero
    and tripped ck_invoices_amount_paid_nonneg (a 500). The row is locked and re-read first: the
    job's lock is already held, so this keeps the Order -> Invoice order."""
    paid = (await db.execute(select(Invoice.amount_paid).where(Invoice.id == inv.id).with_for_update()
                             .execution_options(populate_existing=True))).scalar_one()
    await add_to_column(db, inv, "amount_paid", -min(float(amount), float(paid or 0)))


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
    await _take_off_paid(db, inv, moved)
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
        if not await _billed_rows(db, inv) and not await _has_free_lines(db, inv):
            # Whole jobs: keep the jobs' tax split out, with the total unchanged.
            jt = _jobs_tax(await invoice_orders(db, inv))
            inv.subtotal, inv.tax = q2(sub + float(inv.tax or 0) - jt), jt
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
    jt = _jobs_tax([order])
    inv.subtotal = q2(float(inv.subtotal or 0) + float(order.total or 0) - jt)
    inv.tax = q2(float(inv.tax or 0) + jt)
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
    jt = _jobs_tax([order]) if order and not billed and float(inv.tax or 0) >= _jobs_tax([order]) else 0.0
    inv.subtotal = q2(max(float(inv.subtotal or 0) - (amount - jt), 0))
    inv.tax = q2(float(inv.tax or 0) - jt)
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
                await _take_off_paid(db, inv, amt)
                settle_status(inv)
    await db.flush()
    return out


async def _refuse_gift_card_money_as_cash(db: AsyncSession, order: Order, amount: float) -> None:
    """FIN-09: what gift cards paid on a job goes back to the card (engage.return_to_gift_card),
    never out of a cashbox: a cash refund is capped at the money the customer paid themselves."""
    from backend.routers.engage import gift_card_paid
    from backend.services.order_money import net_paid
    by_card = await gift_card_paid(db, order.id)
    if by_card <= 0:
        return
    own = q2(await net_paid(db, order.id) - by_card)
    if q2(amount) > own + 0.005:
        raise HTTPException(409, f"{by_card:.2f} of what this job was paid came from a gift card. That goes back "
                                 f"to the card (Give back to gift card), not as cash: at most {max(own, 0):.2f} "
                                 "can be refunded here.")


async def refund_payments(db: AsyncSession, order: Order, amount: float, **fields) -> list[Payment]:
    """Money handed back on a job (a refund, a deposit given back, a deleted job), W3-C. It comes
    off money held on the job first, then off the job's invoices, newest first, each lowered by
    what it gives back — so an invoice never stays "paid" for money the customer has back. One
    refund Payment per invoice it touches (invoice_id None = held money). Pass refund_id so voiding
    the refund finds exactly these rows (refund_rows). Caller commits."""
    from sqlalchemy import func
    await lock_order(db, order.id)
    if fields.get("method") != "gift_card":
        await _refuse_gift_card_money_as_cash(db, order, amount)
    held_in, held_out = (float(x or 0) for x in (await db.execute(select(
        func.coalesce(func.sum(case((Payment.kind.in_(MONEY_IN), Payment.amount), else_=0)), 0),
        func.coalesce(func.sum(case((Payment.kind == "refund", func.abs(Payment.amount)), else_=0)), 0))
        .where(Payment.order_id == order.id, Payment.invoice_id.is_(None)))).one())
    left = q2(amount)
    parts: dict = {}
    take = q2(min(left, max(held_in - held_out, 0.0)))
    if take > 0:
        parts[None], left = take, q2(left - take)
    for inv in reversed(await invoices_for_order(db, order.id)):
        if left <= 0:
            break
        rows = (await db.execute(select(Payment.kind, Payment.amount).where(
            Payment.invoice_id == inv.id, Payment.order_id == order.id))).all()
        on_inv = q2(sum(float(a or 0) * (-1 if k == "refund" else 1) for k, a in rows))
        take = q2(min(left, on_inv, float(inv.amount_paid or 0)))
        if take <= 0:
            continue
        parts[inv.id], left = take, q2(left - take)
        await add_to_column(db, inv, "amount_paid", -take)       # take <= amount_paid above
        settle_status(inv)
    if left > 0:
        parts[None] = q2(parts.get(None, 0) + left)
    at = datetime.utcnow()
    fields.setdefault("client_id", order.client_id)
    out = [_new_payment(order_id=order.id, invoice_id=iid, kind="refund", amount=amt, paid_at=at, **fields)
           for iid, amt in parts.items()]
    db.add_all(out)
    await db.flush()
    return out


async def refund_rows(db: AsyncSession, refund, first: Optional[Payment] = None) -> list[Payment]:
    """The live refund Payment rows a Refund wrote (audit DB-11): every row carrying its id. Rows
    written before payments carried refund_id are found from ``first`` (the refund's own row, by
    its payment_id or the caller's legacy match), plus the unlinked rows refund_payments() wrote
    with it (same job, cashbox and paid_at) — matched only among unlinked rows, so another
    refund's linked rows are never taken."""
    linked = list((await db.execute(select(Payment).where(
        Payment.refund_id == refund.id, Payment.kind == "refund"))).scalars().all())
    if linked:
        return linked
    if first is None and refund.payment_id:
        first = (await db.execute(select(Payment).where(
            Payment.id == refund.payment_id))).scalar_one_or_none()
    if first is None or first.kind != "refund" or first.refund_id is not None:
        return []
    if not first.order_id:
        return [first]
    rows = list((await db.execute(select(Payment).where(
        Payment.order_id == first.order_id, Payment.kind == "refund", Payment.refund_id.is_(None),
        Payment.paid_at == first.paid_at, Payment.account_id == first.account_id))).scalars().all())
    # two legacy refunds written in the same second match each other's rows: unless the matches
    # add up to exactly this refund, take only its own row rather than void someone else's money
    if abs(sum(float(p.amount or 0) for p in rows) - float(refund.amount or 0)) > 0.005:
        return [first]
    return rows


async def void_refund_payment(db: AsyncSession, pay: Payment, voided_by_id: Optional[str]) -> None:
    """A refund Payment is voided (its refund was voided), never deleted (audit DB-11): the row
    stays on record with who and when, every read leaves it out (db/soft_void.py), and its
    invoice counts the money again."""
    if pay.voided_at is not None:
        return
    if pay.invoice_id:
        inv = (await db.execute(select(Invoice).where(Invoice.id == pay.invoice_id))).scalar_one_or_none()
        if inv is not None and (inv.status or "") not in VOID_STATUSES:
            await add_to_column(db, inv, "amount_paid", float(pay.amount or 0))
            settle_status(inv)
    from backend.services.dates import now_utc
    pay.voided_at = now_utc()
    pay.voided_by_id = voided_by_id
    await db.flush()


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
