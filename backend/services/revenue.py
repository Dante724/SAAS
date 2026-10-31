"""The shop's money figures, defined once. Every report, dashboard and export reads them here.

Before this, the same day's revenue came out three ways (paid invoices only, payments received,
job totals with tax inside), receivables four ways, and an order refund showed up as an expense.

**Revenue (net sales)**: what the shop earned, ex tax, after returns and refunds.
  + earned work orders: the ex-tax value of their lines, on the day the job was closed or first
    billed on an issued (not draft) invoice, whichever came first — a job paid but still open has
    been sold; a deposit alone has not. A line's total always carries its tax (added on top or already in the price), so its net is
    total / (1 + rate). Estimates and dropped jobs never count.
  + counter sales (POS), on the day of the sale. Deleted (voided) sales never count.
  + invoices that bill no work order (manual invoices): subtotal − discount, on their issue date.
  − credit notes, ex tax, on the day they are issued.
  − refunds of job money, ex tax at the job's own tax share, on the day they are paid. A refund
    that only hands back an overpayment (more paid than billed) was never revenue and doesn't count.
  − card refunds of counter sales, and refunds for goods returned to the counter (no job).

**Tax collected**: the tax on those same lines, less the tax share of refunds and credit notes.

**Cost of goods (COGS)**: cost of the parts on those jobs (returned, restocked parts excluded), the
counter sales' cost, and the costed lines of manual invoices.

**Cash collected** is a different figure, labelled "Paid": money in (job payments, deposits, paid
counter sales) minus money handed back, on the day it moved. Paying from a client's balance
moves no new money and isn't counted.

**Receivables (owed to you)**: what customers still owe on work orders (total − credit notes −
refunds that gave back part of the bill − money paid net of refunds, never below 0; estimates and
dropped jobs excluded) plus the balance of issued manual invoices. Client balances (`ar`) come from the contact ledger: a different measure.

**Operating expenses**: expense records plus expense cash transactions. A refund to a customer
is never an expense, even where a path posts it as one against an order, invoice or sale.

**Low stock**: active items with a minimum set (min_stock > 0) and on hand at or below it.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    ClientReturn,
    ClientReturnLine,
    CreditNote,
    Expense,
    Invoice,
    InvoiceLine,
    InvoiceOrder,
    Order,
    OrderLine,
    OrderStatus,
    Part,
    Payment,
    Refund,
    Sale,
    StockLevel,
    Transaction,
)
from backend.services.invoicing import VOID_STATUSES
from backend.services.money import q2

MONEY_IN = ("payment", "prepayment")
# Some refund paths post the money going back as an `expense` against the document it repays.
CUSTOMER_REF_TYPES = ("order", "invoice", "sale")
_NOT_ISSUED = VOID_STATUSES + ("draft",)


def line_net(total, tax_rate) -> float:
    """Ex-tax value of a job line (its total always includes its tax)."""
    rate = float(tax_rate or 0)
    return float(total or 0) / (1 + rate / 100) if rate else float(total or 0)


def is_customer_refund():
    """SQL condition: a cash transaction that hands money back to a customer."""
    return (Transaction.txn_type == "refund") | (
        (Transaction.txn_type == "expense") & func.coalesce(Transaction.ref_type, "").in_(CUSTOMER_REF_TYPES))


async def _dropped_keys(db: AsyncSession, org_id: str) -> set:
    return set((await db.execute(select(OrderStatus.key).where(
        OrderStatus.org_id == org_id, OrderStatus.status_group == "dropped"))).scalars().all())


def _in(col, df, dt):
    conds = []
    if df is not None:
        conds.append(col >= df)
    if dt is not None:
        conds.append(col <= dt)
    return conds


async def job_figures(db: AsyncSession, orders: list) -> dict:
    """Per job: gross, net and tax of its lines; parts cost (less returned, restocked parts); and
    what credit notes and revenue refunds took off it (ex tax, all time)."""
    ids = [o.id for o in orders]
    out = {o.id: {"gross": 0.0, "net": 0.0, "parts_cost": 0.0, "credit_net": 0.0, "refund_gross": 0.0,
                  "refund_net": 0.0, "credited_gross": 0.0, "paid": 0.0, "refunded": 0.0} for o in orders}
    if not ids:
        return out
    lines = (await db.execute(select(OrderLine).where(OrderLine.order_id.in_(ids)))).scalars().all()
    unit_cost: dict = {}
    for ln in lines:
        f = out[ln.order_id]
        f["gross"] += float(ln.total or 0)
        f["net"] += line_net(ln.total, ln.tax_rate)
        if ln.line_type == "part" or ln.kind == "product":
            f["parts_cost"] += float(ln.cost_price or 0) * float(ln.qty or 0)
            if ln.ref_id:
                unit_cost.setdefault((ln.order_id, ln.ref_id), float(ln.cost_price or 0))
    # goods that came back to the shelf are no longer a cost of the job
    for oid, part_id, qty in (await db.execute(
            select(ClientReturn.order_id, ClientReturnLine.part_id, ClientReturnLine.qty)
            .join(ClientReturnLine, ClientReturnLine.return_id == ClientReturn.id)
            .where(ClientReturn.order_id.in_(ids), ClientReturnLine.restock == True))).all():  # noqa: E712
        out[oid]["parts_cost"] -= unit_cost.get((oid, part_id), 0.0) * float(qty or 0)
    # credit notes on the job's invoices (primary order); ex tax and with tax
    for oid, sub, tot in (await db.execute(
            select(Invoice.order_id, func.sum(CreditNote.subtotal), func.sum(CreditNote.total))
            .join(Invoice, Invoice.id == CreditNote.invoice_id)
            .where(Invoice.order_id.in_(ids), CreditNote.status == "issued")
            .group_by(Invoice.order_id))).all():
        out[oid]["credit_net"] = float(sub or 0)
        out[oid]["credited_gross"] = float(tot or 0)
    for oid, kind, amt in (await db.execute(
            select(Payment.order_id, Payment.kind, func.sum(Payment.amount))
            .where(Payment.order_id.in_(ids)).group_by(Payment.order_id, Payment.kind))).all():
        if kind == "refund":
            out[oid]["refunded"] += abs(float(amt or 0))
        elif kind in MONEY_IN:
            out[oid]["paid"] += float(amt or 0)
    for f in out.values():
        billed = f["gross"] - f["credited_gross"]
        overpaid = max(0.0, f["paid"] - max(billed, 0.0))
        f["refund_gross"] = max(0.0, f["refunded"] - overpaid)
        f["refund_net"] = f["refund_gross"] * (f["net"] / f["gross"]) if f["gross"] else f["refund_gross"]
        f["revenue"] = f["net"] - f["credit_net"] - f["refund_net"]
    return out


async def _billed_on(db, org_id) -> dict:
    """Each job's first issued (not draft, not cancelled) invoice date, as primary or linked job."""
    inv_date = func.min(func.coalesce(Invoice.issued_at, Invoice.created_at))
    live = [Invoice.org_id == org_id, Invoice.status.notin_(_NOT_ISSUED)]
    first: dict = {}
    for q in (select(Invoice.order_id, inv_date).where(*live, Invoice.order_id.isnot(None))
              .group_by(Invoice.order_id),
              select(InvoiceOrder.order_id, inv_date).join(Invoice, Invoice.id == InvoiceOrder.invoice_id)
              .where(*live).group_by(InvoiceOrder.order_id)):
        for oid, d in (await db.execute(q)).all():
            if d is not None and (oid not in first or d < first[oid]):
                first[oid] = d
    return first


async def earned_jobs(db, org_id, df=None, dt=None, location_id=None) -> list:
    """Work orders earned in the period: closed or first billed then, whichever came first."""
    dropped = await _dropped_keys(db, org_id)
    billed = await _billed_on(db, org_id)
    q = select(Order).where(Order.org_id == org_id, Order.is_estimate == False,  # noqa: E712
                            Order.closed_at.isnot(None) | Order.id.in_(list(billed) or ["_"]))
    if dropped:
        q = q.where(Order.status.notin_(dropped))
    if location_id:
        q = q.where(Order.location_id == location_id)
    out = []
    for o in (await db.execute(q)).scalars().all():
        when = min(d for d in (o.closed_at, billed.get(o.id)) if d is not None)
        if (df is None or when >= df) and (dt is None or when <= dt):
            out.append(o)
    return out


def _manual_invoice():
    """SQL condition: an invoice that bills no work order."""
    return Invoice.order_id.is_(None) & ~Invoice.id.in_(select(InvoiceOrder.invoice_id))


async def net_sales(db: AsyncSession, org_id: str, df: Optional[datetime] = None,
                    dt: Optional[datetime] = None, location_id: Optional[str] = None) -> dict:
    """Revenue, tax and COGS for a period (None = open-ended), per the definition above."""
    jobs = await earned_jobs(db, org_id, df, dt, location_id)
    jf = await job_figures(db, jobs)
    job_net = sum(f["net"] for f in jf.values())
    job_tax = sum(f["gross"] - f["net"] for f in jf.values())
    job_cost = sum(f["parts_cost"] for f in jf.values())

    # refunds paid in the period, on any job (closed or not): their revenue share, ex tax
    rq = (select(Payment.order_id, func.sum(Payment.amount)).join(Order, Order.id == Payment.order_id)
          .where(Order.org_id == org_id, Payment.kind == "refund", *_in(Payment.paid_at, df, dt))
          .group_by(Payment.order_id))
    if location_id:
        rq = rq.where(Order.location_id == location_id)
    in_period = {oid: abs(float(a or 0)) for oid, a in (await db.execute(rq)).all()}
    refund_net = refund_tax = 0.0
    if in_period:
        ro = list((await db.execute(select(Order).where(Order.id.in_(list(in_period))))).scalars().all())
        for oid, f in (await job_figures(db, ro)).items():
            if not f["refunded"]:
                continue
            share = in_period[oid] / f["refunded"]
            refund_net += f["refund_net"] * share
            refund_tax += (f["refund_gross"] - f["refund_net"]) * share

    # credit notes issued in the period (a branch sees only those on its own jobs' invoices)
    cq = select(func.coalesce(func.sum(CreditNote.subtotal), 0), func.coalesce(func.sum(CreditNote.tax_total), 0)) \
        .where(CreditNote.org_id == org_id, CreditNote.status == "issued", *_in(CreditNote.issued_at, df, dt))
    if location_id:
        cq = cq.join(Invoice, Invoice.id == CreditNote.invoice_id).join(Order, Order.id == Invoice.order_id) \
            .where(Order.location_id == location_id)
    cn_net, cn_tax = (float(x or 0) for x in (await db.execute(cq)).one())

    # counter sales and their card refunds (a sale's tax, where the till stores it, isn't revenue)
    sale_tax_col = getattr(Sale, "tax", None)
    sale_tax = func.coalesce(func.sum(sale_tax_col), 0) if sale_tax_col is not None else func.sum(0)
    sq = select(func.coalesce(func.sum(Sale.total), 0), func.coalesce(func.sum(Sale.total_cost), 0), sale_tax) \
        .where(Sale.org_id == org_id, Sale.voided_at.is_(None), *_in(Sale.created_at, df, dt))
    if location_id:
        sq = sq.where(Sale.location_id == location_id)
    sales, sales_cost, sales_tax = (float(x or 0) for x in (await db.execute(sq)).one())
    sales -= sales_tax
    # goods brought back to the counter and refunded: a refund tied to no job
    cr = select(func.coalesce(func.sum(Refund.amount), 0)).where(
        Refund.org_id == org_id, Refund.order_id.is_(None), Refund.voided_at.is_(None),
        *_in(Refund.created_at, df, dt))
    if location_id:
        cr = cr.join(ClientReturn, ClientReturn.id == Refund.return_id).where(ClientReturn.location_id == location_id)
    counter_returns = float((await db.execute(cr)).scalar() or 0)
    srq = select(func.coalesce(func.sum(func.abs(Transaction.amount)), 0)).where(
        Transaction.org_id == org_id, Transaction.status == "active", Transaction.ref_type == "sale",
        Transaction.txn_type.in_(("expense", "refund")), *_in(Transaction.actual_date, df, dt))
    if location_id:
        srq = srq.where(Transaction.ref_id.in_(select(Sale.id).where(Sale.location_id == location_id)))
    sale_refunds = float((await db.execute(srq)).scalar() or 0)

    # invoices that bill no job (none for a single branch: they carry no location)
    man_net = man_tax = man_cost = 0.0
    if not location_id:
        inv_date = func.coalesce(Invoice.issued_at, Invoice.created_at)
        mconds = [Invoice.org_id == org_id, Invoice.status.notin_(_NOT_ISSUED), _manual_invoice(),
                  *_in(inv_date, df, dt)]
        man_net, man_tax = (float(x or 0) for x in (await db.execute(select(
            func.coalesce(func.sum(Invoice.subtotal - Invoice.discount), 0),
            func.coalesce(func.sum(Invoice.tax), 0)).where(*mconds))).one())
        man_cost = float((await db.execute(
            select(func.coalesce(func.sum(InvoiceLine.qty * OrderLine.cost_price), 0))
            .select_from(InvoiceLine).join(Invoice, Invoice.id == InvoiceLine.invoice_id)
            .join(OrderLine, OrderLine.id == InvoiceLine.order_line_id)
            .where(*mconds, InvoiceLine.kind == "product"))).scalar() or 0)

    revenue = job_net + sales + man_net - cn_net - refund_net - sale_refunds - counter_returns
    tax = job_tax + sales_tax + man_tax - cn_tax - refund_tax
    return {
        "revenue": q2(revenue), "tax": q2(tax), "gross": q2(revenue + tax),
        "jobs": q2(job_net), "counter_sales": q2(sales), "manual_invoices": q2(man_net),
        "credit_notes": q2(cn_net), "refunds": q2(refund_net + sale_refunds + counter_returns),
        "cogs": q2(job_cost + sales_cost + man_cost), "jobs_count": len(jobs),
    }


async def cash_collected(db: AsyncSession, org_id: str, df: Optional[datetime] = None,
                         dt: Optional[datetime] = None, location_id: Optional[str] = None) -> dict:
    """Money in and money handed back in a period: {"in", "refunds", "net"}."""
    from backend.models import Account
    if location_id:
        mine = Payment.order_id.in_(select(Order.id).where(
            Order.org_id == org_id, Order.location_id == location_id))
    else:
        # Payments carry no org_id: they belong to the shop through their job, invoice or account.
        mine = or_(Payment.order_id.in_(select(Order.id).where(Order.org_id == org_id)),
                   Payment.invoice_id.in_(select(Invoice.id).where(Invoice.org_id == org_id)),
                   Payment.account_id.in_(select(Account.id).where(Account.org_id == org_id)))
    rows = dict((await db.execute(select(Payment.kind == "refund", func.coalesce(func.sum(Payment.amount), 0))
        .where(mine, func.coalesce(Payment.method, "") != "balance", *_in(Payment.paid_at, df, dt))
        .group_by(Payment.kind == "refund"))).all())
    sq = select(func.coalesce(func.sum(Sale.total), 0)).where(
        Sale.org_id == org_id, Sale.paid == True, Sale.voided_at.is_(None), *_in(Sale.created_at, df, dt))  # noqa: E712
    if location_id:
        sq = sq.where(Sale.location_id == location_id)
    money_in = float(rows.get(False, 0) or 0) + float((await db.execute(sq)).scalar() or 0)
    back = abs(float(rows.get(True, 0) or 0))
    return {"in": q2(money_in), "refunds": q2(back), "net": q2(money_in - back)}


async def receivables(db: AsyncSession, org_id: str, location_id: Optional[str] = None,
                      open_only: bool = False) -> float:
    """What customers still owe (see the definition above)."""
    dropped = await _dropped_keys(db, org_id)
    q = select(Order).where(Order.org_id == org_id, Order.is_estimate == False)  # noqa: E712
    if dropped:
        q = q.where(Order.status.notin_(dropped))
    if location_id:
        q = q.where(Order.location_id == location_id)
    if open_only:
        q = q.where(Order.closed_at.is_(None))
    orders = list((await db.execute(q)).scalars().all())
    owed = 0.0
    if orders:
        ids = [o.id for o in orders]
        paid: dict = {}
        for oid, kind, amt in (await db.execute(
                select(Payment.order_id, Payment.kind, func.sum(Payment.amount))
                .where(Payment.order_id.in_(ids)).group_by(Payment.order_id, Payment.kind))).all():
            p, back = paid.get(oid, (0.0, 0.0))
            if kind == "refund":
                back += abs(float(amt or 0))
            elif kind in MONEY_IN:
                p += float(amt or 0)
            paid[oid] = (p, back)
        credited = dict((await db.execute(
            select(Invoice.order_id, func.sum(Invoice.credited))
            .where(Invoice.order_id.in_(ids), Invoice.status.notin_(VOID_STATUSES))
            .group_by(Invoice.order_id))).all())
        for o in orders:
            p, back = paid.get(o.id, (0.0, 0.0))
            billed = float(o.total or 0) - float(credited.get(o.id) or 0)
            # a refund beyond any overpayment gives back part of the bill (a return, goodwill)
            refund_off_bill = max(0.0, back - max(0.0, p - max(billed, 0.0)))
            owed += max(0.0, billed - refund_off_bill - (p - back))
    if not location_id and not open_only:
        owed += float((await db.execute(select(func.coalesce(func.sum(
            Invoice.total - Invoice.amount_paid - Invoice.credited), 0)).where(
            Invoice.org_id == org_id, Invoice.status.notin_(_NOT_ISSUED + ("paid",)), _manual_invoice(),
            (Invoice.total - Invoice.amount_paid - Invoice.credited) > 0))).scalar() or 0)
    return q2(owed)


async def operating_expenses(db: AsyncSession, org_id: str, df: Optional[datetime] = None,
                             dt: Optional[datetime] = None) -> float:
    """Expense records plus expense cash transactions, never a refund to a customer."""
    txn_date = func.coalesce(Transaction.actual_date, Transaction.created_at)
    txns = (await db.execute(select(func.coalesce(func.sum(func.abs(Transaction.amount)), 0)).where(
        Transaction.org_id == org_id, Transaction.txn_type == "expense", Transaction.status == "active",
        ~is_customer_refund(), *_in(txn_date, df, dt)))).scalar() or 0
    econds = [Expense.org_id == org_id]
    if df is not None:
        econds.append(Expense.expense_date >= df.date())
    if dt is not None:
        econds.append(Expense.expense_date <= dt.date())
    recs = (await db.execute(select(func.coalesce(func.sum(Expense.amount), 0)).where(*econds))).scalar() or 0
    return q2(float(txns) + float(recs))


def low_stock_query(org_id: str):
    """Active items with a minimum set, on hand at or under it: (id, name, min_stock, qty) rows,
    counted in the database."""
    on_hand = (select(StockLevel.part_id, func.sum(StockLevel.qty_available).label("qty"))
               .join(Part, Part.id == StockLevel.part_id).where(Part.org_id == org_id)
               .group_by(StockLevel.part_id).subquery())
    qty = func.coalesce(on_hand.c.qty, 0)
    return (select(Part.id, Part.name, Part.min_stock, qty.label("qty"))
            .outerjoin(on_hand, on_hand.c.part_id == Part.id)
            .where(Part.org_id == org_id, Part.active == True,  # noqa: E712
                   func.coalesce(Part.min_stock, 0) > 0, qty <= Part.min_stock))


def low_stock_count_query(org_id: str):
    return select(func.count()).select_from(low_stock_query(org_id).subquery())
