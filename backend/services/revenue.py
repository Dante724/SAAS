"""The shop's money figures, defined once. Every report, dashboard and export reads them here.

Before this, the same day's revenue came out three ways (paid invoices only, payments received,
job totals with tax inside), receivables four ways, and an order refund showed up as an expense.

**Revenue (net sales)**: what the shop earned, ex tax, after returns and refunds.
  + earned work orders: the ex-tax value of their lines, on the day the job was closed or first
    billed on an invoice staff issued (sent to the customer), whichever came first. A payment on
    an open job doesn't earn it: the invoice a payment makes for itself was never issued. A line's
    total always carries its tax (added on top or already in the price), so its net is
    total / (1 + rate). Estimates and deleted jobs never count. Job profitability uses this date too.
  + dropped jobs: only the money the shop kept (paid less refunds, capped at the bill), ex tax,
    on the day the job was dropped. What was written off when it was dropped is never revenue.
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

**Receivables (owed to you)**: what customers still owe on work orders, by the job screen's own
rule (order_money.balance_due: total − written off − money paid net of refunds − credit notes, never below 0;
estimates and dropped jobs excluded), plus the balance of issued manual invoices. Client balances (`ar`) come from the contact ledger: a different measure.

**Operating expenses**: expense records plus expense cash transactions. A refund to a customer
is never an expense, even where a path posts it as one against an order, invoice or sale.

**Low stock**: active items with a minimum set (min_stock > 0) and on hand at or below it.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Select, case, func, or_, select, union
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


async def dropped_keys(db: AsyncSession, org_id: str) -> set:
    """The shop's work-order statuses in the dropped group (the one definition of a dropped job)."""
    return set((await db.execute(select(OrderStatus.key).where(
        OrderStatus.org_id == org_id, OrderStatus.status_group == "dropped",
        OrderStatus.record_type == "work_order"))).scalars().all())


_dropped_keys = dropped_keys


def _in(col, df, dt):
    conds = []
    if df is not None:
        conds.append(col >= df)
    if dt is not None:
        conds.append(col <= dt)
    return conds


def _ids_cond(col, orders):
    """`col IN (...)` for a list of orders/ids or an id SELECT (kept in SQL, so no parameter limit)."""
    if isinstance(orders, Select):
        return col.in_(orders)
    return col.in_([getattr(o, "id", o) for o in orders])


async def job_figures(db: AsyncSession, orders, dropped: Optional[set] = None) -> dict:
    """Per job: gross, net and tax of its lines; parts cost (less returned, restocked parts); what
    credit notes and revenue refunds took off it; and what it earned (all time):
    `charged` (with tax), `revenue` (ex tax) and `tax`. A dropped job earns only the money the
    shop kept (paid less refunds, capped at the bill); what was written off is never revenue.
    `orders` is a list of orders or a SELECT of order ids; the sums run in the database, one row
    per job comes back. `dropped` = the shop's dropped status keys (looked up when not given)."""
    blank = {"gross": 0.0, "net": 0.0, "parts_cost": 0.0, "credit_net": 0.0, "refund_gross": 0.0,
             "refund_net": 0.0, "credited_gross": 0.0, "paid": 0.0, "refunded": 0.0,
             "dropped": False, "written_off": 0.0, "status": None, "total": 0.0}
    out: dict = {} if isinstance(orders, Select) else {getattr(o, "id", o): dict(blank) for o in orders}
    if not isinstance(orders, Select) and not out:
        return out

    def fig(oid):
        return out.setdefault(oid, dict(blank))

    org_of = None
    for oid, status, org, total, wo in (await db.execute(
            select(Order.id, Order.status, Order.org_id, Order.total, Order.written_off)
            .where(_ids_cond(Order.id, orders)))).all():
        f = fig(oid)
        f["status"], f["total"], f["written_off"] = status, float(total or 0), float(wo or 0)
        org_of = org_of or org
    if dropped is None:
        dropped = await _dropped_keys(db, org_of) if org_of else set()

    is_part = (OrderLine.line_type == "part") | (OrderLine.kind == "product")
    rate = func.coalesce(OrderLine.tax_rate, 0)
    for oid, gross, net, cost in (await db.execute(
            select(OrderLine.order_id, func.sum(OrderLine.total),
                   func.sum(OrderLine.total / (1 + rate / 100.0)),
                   func.sum(case((is_part, func.coalesce(OrderLine.cost_price, 0) * OrderLine.qty), else_=0)))
            .where(_ids_cond(OrderLine.order_id, orders),
                   # A refund's own line (returns.REFUND_LINE_KIND) lowers what the customer owes on
                   # the job screen; for revenue the job keeps its sold value and the refund comes
                   # off below, once, on the day it was paid.
                   func.coalesce(OrderLine.kind, "") != "refund")
            .group_by(OrderLine.order_id))).all():
        f = fig(oid)
        f["gross"], f["net"], f["parts_cost"] = float(gross or 0), float(net or 0), float(cost or 0)
    # goods that came back to the shelf are no longer a cost of the job (at the job's cost for that item)
    unit = (select(OrderLine.order_id, OrderLine.ref_id, func.min(OrderLine.cost_price).label("cost"))
            .where(is_part, OrderLine.ref_id.isnot(None), _ids_cond(OrderLine.order_id, orders))
            .group_by(OrderLine.order_id, OrderLine.ref_id).subquery())
    for oid, back in (await db.execute(
            select(ClientReturn.order_id, func.sum(func.coalesce(unit.c.cost, 0) * ClientReturnLine.qty))
            .join(ClientReturnLine, ClientReturnLine.return_id == ClientReturn.id)
            .join(unit, (unit.c.order_id == ClientReturn.order_id) & (unit.c.ref_id == ClientReturnLine.part_id))
            .where(ClientReturnLine.restock == True)  # noqa: E712
            .group_by(ClientReturn.order_id))).all():
        fig(oid)["parts_cost"] -= float(back or 0)
    # A dropped job puts its parts back on the shelf (orders._return_parts_on_drop marks them
    # pending_stock again): those aren't a cost of the job either.
    if dropped:
        for oid, back in (await db.execute(
                select(OrderLine.order_id,
                       func.sum(func.coalesce(OrderLine.cost_price, 0) * func.coalesce(OrderLine.pending_stock, 0)))
                .join(Order, Order.id == OrderLine.order_id)
                .where(is_part, _ids_cond(OrderLine.order_id, orders), Order.status.in_(list(dropped)))
                .group_by(OrderLine.order_id))).all():
            f = fig(oid)
            f["parts_cost"] = max(0.0, f["parts_cost"] - float(back or 0))
    # credit notes on the invoices billing the job, ex tax and with tax — an invoice billing several
    # jobs shares them out by job total, the same split the job screen uses (order_money)
    from backend.services.order_money import invoice_job_shares
    sh = invoice_job_shares()
    for oid, sub, tot in (await db.execute(
            select(sh.c.order_id, func.sum(CreditNote.subtotal * sh.c.share), func.sum(CreditNote.total * sh.c.share))
            .join(Invoice, Invoice.id == sh.c.invoice_id)
            .join(CreditNote, CreditNote.invoice_id == Invoice.id)
            .where(_ids_cond(sh.c.order_id, orders), CreditNote.status == "issued",
                   Invoice.status.notin_(VOID_STATUSES))
            .group_by(sh.c.order_id))).all():
        f = fig(oid)
        f["credit_net"], f["credited_gross"] = float(sub or 0), float(tot or 0)
    for oid, paid, back in (await db.execute(
            select(Payment.order_id,
                   func.sum(case((Payment.kind.in_(MONEY_IN), Payment.amount), else_=0)),
                   func.sum(case((Payment.kind == "refund", func.abs(Payment.amount)), else_=0)))
            .where(_ids_cond(Payment.order_id, orders)).group_by(Payment.order_id))).all():
        f = fig(oid)
        f["paid"], f["refunded"] = float(paid or 0), float(back or 0)
    for f in out.values():
        billed = f["gross"] - f["credited_gross"]
        overpaid = max(0.0, f["paid"] - max(billed, 0.0))
        f["refund_gross"] = max(0.0, f["refunded"] - overpaid)
        f["refund_net"] = f["refund_gross"] * (f["net"] / f["gross"]) if f["gross"] else f["refund_gross"]
        f["revenue"] = f["net"] - f["credit_net"] - f["refund_net"]
        f["charged"] = billed - f["refund_gross"]
        f["dropped"] = f["status"] in dropped
        if f["dropped"]:
            # the money kept, ex tax at the job's own tax share
            f["charged"] = min(max(f["paid"] - f["refunded"], 0.0), max(billed, 0.0))
            f["revenue"] = f["charged"] * (f["net"] / f["gross"]) if f["gross"] else f["charged"]
        else:
            f["written_off"] = 0.0
        f["tax"] = f["charged"] - f["revenue"]
    return out


def _billed_on(org_id):
    """Each job's first invoice staff actually issued (sent to the customer, not cancelled), as
    primary or linked job: (order_id, billed_at). The invoice a payment makes on its own is a
    draft that only turns partial/paid; it was never issued, so it doesn't bill the job."""
    live = [Invoice.org_id == org_id, Invoice.sent_at.isnot(None), Invoice.status.notin_(VOID_STATUSES)]
    jobs = union(
        select(Invoice.order_id.label("order_id"), Invoice.sent_at.label("at"))
        .where(*live, Invoice.order_id.isnot(None)),
        select(InvoiceOrder.order_id.label("order_id"), Invoice.sent_at.label("at"))
        .join(Invoice, Invoice.id == InvoiceOrder.invoice_id).where(*live)).subquery()
    return select(jobs.c.order_id, func.min(jobs.c.at).label("billed_at")).group_by(jobs.c.order_id).subquery()


async def earned_jobs_query(db, org_id, df=None, dt=None, location_id=None, cols=None, *,
                            include_dropped: bool = False, technician_id: Optional[str] = None,
                            allowed_locations=None, with_date: bool = False):
    """SELECT of the work orders earned in the period — closed, or first billed on an issued
    invoice, whichever came first — with the date filter applied in the database. The one date
    rule for every report that counts a job's revenue. Estimates and deleted (archived) jobs never
    count; dropped jobs only with include_dropped (they earn the money they kept, see job_figures).
    with_date=True returns (Order, earned_at) rows."""
    billed = _billed_on(org_id)
    dropped = await _dropped_keys(db, org_id)
    earned_at = case((Order.closed_at.is_(None), billed.c.billed_at),
                     (billed.c.billed_at.is_(None), Order.closed_at),
                     *([(Order.status.in_(dropped), Order.closed_at)] if dropped else []),   # the day it was dropped
                     (billed.c.billed_at < Order.closed_at, billed.c.billed_at),
                     else_=Order.closed_at)
    cols = cols if cols is not None else ((Order, earned_at.label("earned_at")) if with_date else (Order,))
    q = (select(*cols).select_from(Order)
         .outerjoin(billed, billed.c.order_id == Order.id)
         .where(Order.org_id == org_id, Order.is_estimate == False, Order.archived == False,  # noqa: E712
                Order.closed_at.isnot(None) | billed.c.billed_at.isnot(None), *_in(earned_at, df, dt)))
    if not include_dropped and dropped:
        q = q.where(Order.status.notin_(dropped))
    if location_id:
        q = q.where(Order.location_id == location_id)
    if allowed_locations is not None:
        q = q.where(Order.location_id.in_(list(allowed_locations) or [""]))
    if technician_id:
        q = q.where(Order.assigned_to_id == technician_id)
    return q


async def earned_jobs(db, org_id, df=None, dt=None, location_id=None) -> list:
    """Work orders earned in the period (see earned_jobs_query)."""
    return list((await db.execute(await earned_jobs_query(db, org_id, df, dt, location_id))).scalars().all())


def _manual_invoice():
    """SQL condition: an invoice that bills no work order."""
    return Invoice.order_id.is_(None) & ~Invoice.id.in_(select(InvoiceOrder.invoice_id))


async def net_sales(db: AsyncSession, org_id: str, df: Optional[datetime] = None,
                    dt: Optional[datetime] = None, location_id: Optional[str] = None) -> dict:
    """Revenue, tax and COGS for a period (None = open-ended), per the definition above."""
    dropped = await _dropped_keys(db, org_id)
    jf = await job_figures(db, await earned_jobs_query(db, org_id, df, dt, location_id, cols=(Order.id,),
                                                       include_dropped=True), dropped)
    # a dropped job adds what it kept (net of its refunds and credit notes already)
    job_net = sum(f["revenue"] if f["dropped"] else f["net"] for f in jf.values())
    job_tax = sum(f["tax"] if f["dropped"] else f["gross"] - f["net"] for f in jf.values())
    job_cost = sum(f["parts_cost"] for f in jf.values())

    # refunds paid in the period, on any job (closed or not): their revenue share, ex tax
    rq = (select(Payment.order_id, func.sum(Payment.amount)).join(Order, Order.id == Payment.order_id)
          .where(Order.org_id == org_id, Payment.kind == "refund", *_in(Payment.paid_at, df, dt),
                 *([Order.status.notin_(dropped)] if dropped else []))
          .group_by(Payment.order_id))
    if location_id:
        rq = rq.where(Order.location_id == location_id)
    in_period = {oid: abs(float(a or 0)) for oid, a in (await db.execute(rq)).all()}
    refund_net = refund_tax = 0.0
    if in_period:
        for oid, f in (await job_figures(db, rq.with_only_columns(Payment.order_id).group_by(None)
                                                .distinct())).items():
            if oid not in in_period or not f["refunded"]:
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
    if dropped:   # a dropped job's share of its invoices' credit notes is already inside what it kept
        from backend.services.order_money import invoice_job_shares
        sh = invoice_job_shares()
        dq = (select(func.coalesce(func.sum(CreditNote.subtotal * sh.c.share), 0),
                     func.coalesce(func.sum(CreditNote.tax_total * sh.c.share), 0))
              .select_from(CreditNote).join(sh, sh.c.invoice_id == CreditNote.invoice_id)
              .join(Order, Order.id == sh.c.order_id)
              .where(CreditNote.org_id == org_id, CreditNote.status == "issued", Order.status.in_(dropped),
                     *_in(CreditNote.issued_at, df, dt)))
        if location_id:
            dq = dq.where(Order.location_id == location_id)
        d_net, d_tax = (float(x or 0) for x in (await db.execute(dq)).one())
        cn_net, cn_tax = cn_net - d_net, cn_tax - d_tax

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
        "cogs": q2(job_cost + sales_cost + man_cost), "jobs_count": len(jf),
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
    is_refund = func.coalesce(Payment.kind, "payment") == "refund"
    paid_in, paid_back = (await db.execute(select(
        func.coalesce(func.sum(case((is_refund, 0), else_=Payment.amount)), 0),
        func.coalesce(func.sum(case((is_refund, Payment.amount), else_=0)), 0))
        .where(mine, func.coalesce(Payment.method, "") != "balance", *_in(Payment.paid_at, df, dt)))).one()
    sq = select(func.coalesce(func.sum(Sale.total), 0)).where(
        Sale.org_id == org_id, Sale.paid == True, Sale.voided_at.is_(None), *_in(Sale.created_at, df, dt))  # noqa: E712
    if location_id:
        sq = sq.where(Sale.location_id == location_id)
    money_in = float(paid_in or 0) + float((await db.execute(sq)).scalar() or 0)
    back = abs(float(paid_back or 0))
    return {"in": q2(money_in), "refunds": q2(back), "net": q2(money_in - back)}


async def receivables(db: AsyncSession, org_id: str, location_id: Optional[str] = None,
                      open_only: bool = False) -> float:
    """What customers still owe (see the definition above), summed in the database."""
    from backend.services.order_money import balances_due
    dropped = await _dropped_keys(db, org_id)
    due = balances_due(org_id)
    q = select(func.coalesce(func.sum(due.c.owed), 0)).select_from(due).join(Order, Order.id == due.c.order_id).where(
        Order.org_id == org_id, Order.is_estimate == False)  # noqa: E712
    if dropped:
        q = q.where(Order.status.notin_(dropped))
    if location_id:
        q = q.where(Order.location_id == location_id)
    if open_only:
        q = q.where(Order.closed_at.is_(None))
    owed = float((await db.execute(q)).scalar() or 0)
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
