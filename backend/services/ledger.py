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
    income          a payment received against an order                      (-)
    prepayment      money taken before the work is billed                    (-)
    refund          an over-payment handed back to the client                (+)
    advance         manual: client pays money in                             (-)
    payout          manual: we return money to the client                    (+)
    adjustment      manual: signed correction                                (±)
    posting         goods received from a supplier on credit                 (-)
    purchase_return goods returned to a supplier on credit                   (+)
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Client, ClientLedgerEntry
from backend.services.money import q2, q4


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
        c.balance = q2(float(c.balance or 0) + amt)
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
    supplier.balance = q2(float(supplier.balance or 0) + d)
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
            if not p.client_id or await _has_ref(db, p.client_id, "payment", p.id):
                continue
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
