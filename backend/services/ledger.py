"""Build 124 — the unified contact settlement ledger.

Since build 114 a supplier *is* a contact (``Supplier.client_id`` points back at the
unified ``Client`` row), so there is exactly **one** ledger per contact and one honest
balance, whether that contact is a customer, a vendor, or both. Every mutual movement of
goods, services and cash appends a row here and moves the contact's running balance in
lockstep, so the Balance tab can show a full, audited statement the way Orderry does
instead of a bare number.

Sign convention (single source of truth):

    +amount  → the contact owes us      (accounts receivable)
    -amount  → we owe the contact       (store credit / accounts payable)

Rows are written through :func:`post_contact_ledger` only, so the ledger and the
``Client.balance`` mirror can never silently disagree.

Entry types used across the app:
    charge          a completed work order's total is billed to the client   (+)
                    (ref_type "invoice_payment": invoice money paid for no job)
    income          a payment received against an order                      (-)
    prepayment      money taken before the work is billed                    (-)
    refund          an over-payment handed back to the client                (+)
    advance         manual: client pays money in                             (-)
    payout          manual: we return money to the client                    (+)
    adjustment      manual: signed correction                                (±)
    write_off       unpaid balance of a dropped job written off (reversed on reopen) (±)
    posting         goods received from a supplier on credit                 (-)
    purchase_return goods returned to a supplier on credit                   (+)
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Client, ClientLedgerEntry
from backend.services.money import q2


async def post_contact_ledger(
    db: AsyncSession,
    org_id: str,
    client_id: Optional[str],
    entry_type: str,
    amount: float,
    *,
    note: Optional[str] = None,
    ref_type: Optional[str] = None,
    ref_id: Optional[str] = None,
    created_by_id: Optional[str] = None,
) -> Optional[ClientLedgerEntry]:
    """Append one movement to a contact's ledger and move its balance by ``amount``.

    Returns the created entry, or ``None`` when there is no contact to post against or
    the amount rounds to zero (so callers can fire unconditionally). Does not commit —
    the caller owns the transaction boundary.
    """
    if not client_id:
        return None
    amt = q2(float(amount or 0))
    if amt == 0:
        return None
    entry = ClientLedgerEntry(
        org_id=org_id, client_id=client_id, entry_type=entry_type, amount=amt,
        note=note, ref_type=ref_type, ref_id=ref_id, created_by_id=created_by_id,
    )
    db.add(entry)
    c = (await db.execute(select(Client).where(
        Client.id == client_id, Client.org_id == org_id))).scalar_one_or_none()
    if c is not None:
        from backend.services.money import add_to_column
        await add_to_column(db, c, "balance", amt)
    return entry


async def adjust_supplier_ap(
    db: AsyncSession,
    org_id: str,
    supplier,
    delta: float,
    *,
    entry_type: str = "posting",
    note: Optional[str] = None,
    ref_type: Optional[str] = None,
    ref_id: Optional[str] = None,
    created_by_id: Optional[str] = None,
) -> float:
    """The single write-path for a supplier's AP balance (build 144ax).

    ``Supplier.balance`` (a running column, + = we owe them) and the linked contact ledger
    (- = we owe them) are two views of the same debt. Previously every receipt/return/posting
    site updated both by hand, which let them drift — e.g. the column took a raw float while the
    ledger took a quantized one, or a delete reversed the column without the ledger. This routes
    both through one function so they move by the SAME quantized amount, every time.

    ``delta`` is the supplier-side change (+delta = we owe more). The column moves by +delta and
    the contact ledger by -delta. A supplier with no linked contact just moves the column. Does
    not commit — the caller owns the transaction.
    """
    d = q2(delta)
    from backend.services.money import add_to_column
    await add_to_column(db, supplier, "balance", d)
    await post_contact_ledger(db, org_id, getattr(supplier, "client_id", None), entry_type, -d,
                              note=note, ref_type=ref_type, ref_id=ref_id, created_by_id=created_by_id)
    return supplier.balance


async def order_charge_net(db: AsyncSession, client_id: Optional[str], order_id: str) -> float:
    """Net of every ``charge`` row already posted for one order.

    Used to make close/reopen idempotent and to absorb total edits: on close we post the
    *difference* needed to reach the order total; on reopen we post the negative of the
    running net. Repeated closes therefore never double-bill.
    """
    if not client_id:
        return 0.0
    total = (await db.execute(
        select(func.coalesce(func.sum(ClientLedgerEntry.amount), 0)).where(
            ClientLedgerEntry.client_id == client_id,
            ClientLedgerEntry.ref_type == "order",
            ClientLedgerEntry.ref_id == order_id,
            ClientLedgerEntry.entry_type == "charge",
        )
    )).scalar() or 0
    return q2(float(total))


async def credit_paid(db: AsyncSession, order_id: Optional[str]) -> float:
    """What has been paid on one job out of client credit ("pay from balance"), net of any
    refund booked against the balance."""
    if not order_id:
        return 0.0
    from sqlalchemy import case

    from backend.models import Payment
    amt = func.abs(func.coalesce(Payment.amount, 0))
    return q2(float((await db.execute(select(func.coalesce(func.sum(
        case((Payment.kind == "refund", -amt), else_=amt)), 0)).where(
        Payment.order_id == order_id, Payment.method == "balance"))).scalar() or 0))


async def credit_spent_target(db: AsyncSession, order) -> float:
    """The charge a job that isn't closed carries on the ledger: the client credit already spent
    on it (never more than the job is worth). A job paid from credit keeps that credit spent while
    it moves between open statuses or is reopened, so the credit can't pay a second job (H6). An
    archived job carries nothing: archiving gives that credit back."""
    if getattr(order, "archived", False):
        return 0.0
    worth = float(order.total or 0) - float(getattr(order, "written_off", 0) or 0)
    return q2(max(0.0, min(worth, await credit_paid(db, order.id))))


def _open_jobs(org_id: str, client_id: str):
    """The client's jobs whose money is still held for them: not archived and not closed (an
    estimate, or a work order in a status that isn't terminal, as the status change decides it,
    with the same built-in fallback)."""
    from sqlalchemy import or_

    from backend.models import Order, OrderStatus
    active = select(OrderStatus.key).where(OrderStatus.org_id == org_id, OrderStatus.active == True,  # noqa: E712
                                           OrderStatus.record_type == "work_order")
    terminal = active.where(OrderStatus.is_terminal == True)  # noqa: E712
    status = func.coalesce(Order.status, "")
    return select(Order.id).where(
        Order.org_id == org_id, Order.client_id == client_id,
        func.coalesce(Order.archived, False) == False,  # noqa: E712
        or_(Order.is_estimate == True,  # noqa: E712
            ~status.in_(terminal) & (status.in_(active) | ~status.in_(("done", "closed", "delivered")))))


async def _held_rows(db: AsyncSession, org_id: str, client_id: str, order_id: Optional[str]) -> float:
    """Net of the ledger rows that aren't free credit when paying for ``order_id``: that job's own
    rows (its charges and the money taken for it), the money taken for every other open job of
    the client (held for that job until it is billed; its credit-spend charge still counts), and
    a credit note on another open job's invoice (it lowers what that job owes when it is billed,
    so it isn't money to pay a different job with; B3 recheck review)."""
    from sqlalchemy import or_

    from backend.models import CreditNote, Invoice, InvoiceOrder, Order, Payment
    open_jobs = _open_jobs(org_id, client_id)
    held = Payment.order_id.in_(open_jobs)
    if order_id:
        held = or_(held, Payment.order_id == order_id)
    conds = (ClientLedgerEntry.ref_type == "payment") & ClientLedgerEntry.ref_id.in_(
        select(Payment.id).where(held))
    other_open = open_jobs.where(Order.id != order_id) if order_id else open_jobs
    noted_invoices = select(Invoice.id).where(Invoice.order_id.in_(other_open)).union(
        select(InvoiceOrder.invoice_id).where(InvoiceOrder.order_id.in_(other_open)))
    conds = conds | ((ClientLedgerEntry.ref_type == "credit_note") & ClientLedgerEntry.ref_id.in_(
        select(CreditNote.id).where(CreditNote.invoice_id.in_(noted_invoices))))
    if order_id:
        conds = conds | ((ClientLedgerEntry.ref_type == "order") & (ClientLedgerEntry.ref_id == order_id))
    return float((await db.execute(select(func.coalesce(func.sum(ClientLedgerEntry.amount), 0)).where(
        ClientLedgerEntry.client_id == client_id, conds))).scalar() or 0)


async def available_credit(db: AsyncSession, org_id: str, client_id: Optional[str],
                           order_id: Optional[str] = None) -> float:
    """Client money the shop already holds that can pay for something (B3, H6).

    It is the balance without the rows of :func:`_held_rows` (this job's charge and the money
    taken for it, and money held on the client's other open jobs), less what this job has already
    been paid from credit: that part is earmarked against its charge. A "pay from balance" moves
    no money: it marks the job paid out of that credit and spends it through
    :func:`spend_credit`. Money for an invoice's own (job-less) lines paid by other methods is
    billed as it is paid (:func:`charge_jobless_invoice_money`), so it never reads as credit."""
    if not client_id:
        return 0.0
    # No row lock here: two balance-pays at once are serialised by spend_credit's guarded UPDATE.
    bal = (await db.execute(select(Client.balance).where(
        Client.id == client_id, Client.org_id == org_id))).scalar() or 0
    held = await _held_rows(db, org_id, client_id, order_id)
    return q2(max(0.0, -(float(bal) - held) - await credit_paid(db, order_id)))


async def charge_jobless_invoice_money(db: AsyncSession, org_id: str, inv, payments,
                                       created_by_id: str | None = None) -> None:
    """Bill the part of an invoice payment that belongs to no job (B3 recheck).

    A job is billed to the ledger when it closes; an invoice's own lines (a standalone invoice, or
    free lines beside its jobs) never are. So money taken for them read as client credit, and a
    "pay from balance" for them never used any credit up. Each job-less payment is billed as it
    is paid (cash nets to zero against its income row; a balance payment is charged by
    :func:`spend_credit` instead), and
    change handed back (a job-less refund row) takes its share off again. Caller commits."""
    for p in payments:
        if p is None or p.order_id or not inv.client_id:
            continue
        if p.method == "balance":
            continue        # spend_credit charges a job-less balance share to the invoice itself
        amt = -abs(float(p.amount or 0)) if p.kind == "refund" else float(p.amount or 0)
        await post_contact_ledger(db, org_id, inv.client_id, "charge", amt,
                                  note=f"Invoice {inv.invoice_number or ''}".strip(), ref_type="invoice_payment",
                                  ref_id=p.id, created_by_id=created_by_id)


async def spend_credit(db: AsyncSession, org_id: str, client_id: str, amount: float, *,
                       order_id: Optional[str], parts, invoice_id: Optional[str] = None,
                       label: str = "", created_by_id: Optional[str] = None) -> None:
    """A job or invoice paid from client credit spends that credit now, not at the job's close.

    Call it after the balance payment rows are added. Marking the job paid without a ledger row
    left the credit on the balance, so the same 100 of credit paid any number of open jobs. Each
    job's share is charged to the ledger up front: its charge is raised to
    :func:`credit_spent_target`, which the status change also keeps an open job at (so moving it
    on or reopening it never hands the credit back) and which the close then tops up to the
    total, so the job is never billed twice; a share with no job is charged to the invoice itself.
    The balance moves by one guarded UPDATE that only goes through while the credit (the check
    available_credit makes, taken on the row) still covers ``amount``, so two balance-pays at once
    can't both spend it. ``parts`` are (order_id | None, amount) pairs, as
    apply_payment_to_invoice splits the payment."""
    from fastapi import HTTPException
    from sqlalchemy import update

    from backend.models import Order
    await db.flush()
    rows: list[tuple[str, str, float, str]] = []
    share = 0.0
    for oid, amt in parts:
        amt = q2(float(amt or 0))
        if amt <= 0:
            continue
        if oid and oid == order_id:
            share = q2(share + amt)
        if oid:
            o = (await db.execute(select(Order).where(Order.id == oid))).scalar_one_or_none()
            target = await credit_spent_target(db, o) if o else 0.0
            early = q2(min(amt, max(0.0, target - await order_charge_net(db, client_id, oid))))
            if early > 0:
                rows.append(("order", oid, early, f"Paid from client credit: {o.order_number}"))
        elif invoice_id:
            rows.append(("invoice", invoice_id, amt, f"Paid from client credit: invoice {label}"))
    held = await _held_rows(db, org_id, client_id, order_id)
    earmarked = q2(await credit_paid(db, order_id) - share)        # paid from credit before this one
    delta = q2(sum(r[2] for r in rows))
    res = await db.execute(update(Client).where(
        Client.id == client_id, Client.org_id == org_id,
        func.coalesce(Client.balance, 0) <= q2(held - earmarked - float(amount)) + 0.005)
        .values(balance=func.coalesce(Client.balance, 0) + delta)
        .execution_options(synchronize_session=False))
    if res.rowcount != 1:
        credit = q2(max(0.0, -(float((await db.execute(select(Client.balance).where(
            Client.id == client_id))).scalar() or 0) - held) - earmarked))
        raise HTTPException(400, f"This client has only {credit:.2f} of credit to pay from. "
                                 "Take the rest in cash or by card.")
    for ref_type, ref_id, amt, note in rows:
        db.add(ClientLedgerEntry(org_id=org_id, client_id=client_id, entry_type="charge", amount=amt,
                                 note=note, ref_type=ref_type, ref_id=ref_id, created_by_id=created_by_id))
    c = (await db.execute(select(Client).where(Client.id == client_id))).scalar_one_or_none()
    if c is not None:
        await db.refresh(c, attribute_names=["balance"])


async def _has_ref(db: AsyncSession, client_id: str, ref_type: str, ref_id: str) -> bool:
    n = (await db.execute(
        select(func.count()).select_from(ClientLedgerEntry).where(
            ClientLedgerEntry.client_id == client_id,
            ClientLedgerEntry.ref_type == ref_type,
            ClientLedgerEntry.ref_id == ref_id,
        )
    )).scalar() or 0
    return n > 0


async def rebuild_org_ledger(db: AsyncSession, org_id: str, created_by_id: str | None = None) -> dict:
    """Build 125 — reconstruct the unified ledger from source documents, idempotently.

    Historical work (before the ledger auto-posted) leaves the Balance tab blank even
    though the orders, payments, postings and returns all exist. This walks those source
    records and synthesises the ledger rows that would exist had the app always posted
    them, keyed by source id so re-running never duplicates. It then sets each touched
    contact's balance to the sum of its ledger, so any pre-ledger drift is superseded by a
    fully derived number.

    Safe to run repeatedly; intended to be run once when adopting the unified ledger.
    Returns a summary of what was (re)built.
    """
    from backend.models import Client, Order, Payment, Posting, PurchaseReturn, Supplier
    summary = {"orders_charged": 0, "payments_posted": 0, "postings_posted": 0,
               "returns_posted": 0, "contacts_rebalanced": 0}
    affected: set[str] = set()

    # 1) Charge for every closed (non-estimate) order, reconciled to its total.
    orders = (await db.execute(select(Order).where(
        Order.org_id == org_id, Order.is_estimate == False,          # noqa: E712
        Order.closed_at.isnot(None), Order.client_id.isnot(None)))).scalars().all()
    for o in orders:
        net = await order_charge_net(db, o.client_id, o.id)
        delta = q2(float(o.total or 0) - net)
        if delta:
            await post_contact_ledger(db, org_id, o.client_id, "charge", delta,
                                      note=f"Order {o.order_number} ready", ref_type="order",
                                      ref_id=o.id, created_by_id=created_by_id)
            summary["orders_charged"] += 1
            affected.add(o.client_id)

    # 2) One row per payment (income / prepayment / refund), keyed by payment id.
    client_ids = [cid for (cid,) in (await db.execute(
        select(Client.id).where(Client.org_id == org_id))).all()]
    if client_ids:
        pays = (await db.execute(select(Payment).where(
            Payment.client_id.in_(client_ids)))).scalars().all()
        for p in pays:
            if not p.client_id or p.method == "balance" or await _has_ref(db, p.client_id, "payment", p.id):
                continue   # paid from credit the ledger already holds: no movement of its own
            if p.kind == "refund":
                et, amt = "refund", abs(float(p.amount or 0))
            elif p.kind == "prepayment":
                et, amt = "prepayment", -abs(float(p.amount or 0))
            else:
                et, amt = "income", -abs(float(p.amount or 0))
            onum = None
            if p.order_id:
                onum = (await db.execute(select(Order.order_number).where(
                    Order.id == p.order_id))).scalar()
            note = f"{et.capitalize()} for {onum}" if onum else et.capitalize()
            await post_contact_ledger(db, org_id, p.client_id, et, amt, note=note,
                                      ref_type="payment", ref_id=p.id, created_by_id=created_by_id)
            summary["payments_posted"] += 1
            affected.add(p.client_id)

    # 3) Supplier postings settled on balance (we owe them → negative).
    postings = (await db.execute(select(Posting).where(
        Posting.org_id == org_id, Posting.status == "posted",
        Posting.settlement == "balance", Posting.supplier_id.isnot(None)))).scalars().all()
    for doc in postings:
        sup = (await db.execute(select(Supplier).where(Supplier.id == doc.supplier_id))).scalar_one_or_none()
        if not sup or not sup.client_id or await _has_ref(db, sup.client_id, "posting", doc.id):
            continue
        await post_contact_ledger(db, org_id, sup.client_id, "posting", -float(doc.total or 0),
                                  note=f"Posting {doc.number or ''}".strip(), ref_type="posting",
                                  ref_id=doc.id, created_by_id=created_by_id)
        summary["postings_posted"] += 1
        affected.add(sup.client_id)

    # 4) Purchase returns settled on balance (we owe them less → positive).
    returns = (await db.execute(select(PurchaseReturn).where(
        PurchaseReturn.org_id == org_id, PurchaseReturn.status == "posted",
        PurchaseReturn.settlement == "balance", PurchaseReturn.supplier_id.isnot(None)))).scalars().all()
    for doc in returns:
        sup = (await db.execute(select(Supplier).where(Supplier.id == doc.supplier_id))).scalar_one_or_none()
        if not sup or not sup.client_id or await _has_ref(db, sup.client_id, "purchase_return", doc.id):
            continue
        await post_contact_ledger(db, org_id, sup.client_id, "purchase_return", float(doc.total or 0),
                                  note=f"Return {doc.number or ''}".strip(), ref_type="purchase_return",
                                  ref_id=doc.id, created_by_id=created_by_id)
        summary["returns_posted"] += 1
        affected.add(sup.client_id)

    # 5) Make each touched contact's balance a fully-derived sum of its ledger.
    await db.flush()
    for cid in affected:
        total = (await db.execute(select(func.coalesce(func.sum(ClientLedgerEntry.amount), 0))
                 .where(ClientLedgerEntry.client_id == cid))).scalar() or 0
        c = (await db.execute(select(Client).where(Client.id == cid))).scalar_one_or_none()
        if c is not None:
            c.balance = q2(float(total))
            summary["contacts_rebalanced"] += 1
    await db.commit()
    return summary
