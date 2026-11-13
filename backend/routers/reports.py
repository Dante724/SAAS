import asyncio
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import case, select, func
from sqlalchemy.orm import defer, selectinload
from backend.db.database import get_db
from backend.models import Order, Client, Invoice, Part, StockLevel, Task, Employee
from backend.services.auth import get_current_employee, require_perm
from backend.services.dates import (
    day_start_utc, now_utc, range_edge, report_edge, shop_today, shop_tzname, today_in, utc_to_wall,
)
# The money figures and low stock are defined once, in services/revenue (read its docstring).
from backend.services import revenue as money_rules, stock_rows
from backend.services.revenue import low_stock_count_query, low_stock_query, line_net
from backend.services.task_visibility import assigned_sql, visibility_conds

# Product photos (parts.image_data, up to 400 KB each) are never needed to print a report row
# (PERF-03): every report that reads Part rows defers the column.
_NO_PHOTO = defer(Part.image_data)

router = APIRouter(prefix="/api/reports", tags=["reports"])

# Short per-org cache for the dashboard tiles: every reports visit (and every tab) used to
# re-run all of these aggregates. Shop-wide numbers, the same for everyone allowed to see
# them, so a few seconds of staleness is harmless (no invalidation: up to _DASH_TTL_S stale).
# With REDIS_URL set the entry lives in Redis (`dash:{org_id}`), shared by every worker;
# without Redis, or while it is unreachable, the bounded in-process dict below is used and
# the request never fails because of the cache.
_DASH_TTL_S = 30
_dash_cache: dict = {}   # org_id -> (expires_at, payload)


async def _dash_get(oid: str, *, key=None, redis_key=None):
    import json
    import time

    from backend.services import ratelimit
    r = await ratelimit.get_aredis()
    if r is not None:
        try:
            raw = await r.get(redis_key or f"dash:{oid}")
            return json.loads(raw) if raw else None
        except Exception:   # noqa: BLE001 — Redis down or bad entry: fall back in-process
            ratelimit.aredis_failed()
    hit = _dash_cache.get(key or oid)
    return hit[1] if hit and hit[0] > time.monotonic() else None


async def _dash_put(oid: str, out: dict, *, key=None, redis_key=None, ttl: int = _DASH_TTL_S) -> None:
    import time

    from backend.services import ratelimit
    from backend.services.money import json_dumps
    r = await ratelimit.get_aredis()
    if r is not None:
        try:
            await r.set(redis_key or f"dash:{oid}", json_dumps(out), ex=ttl)
            return
        except Exception:   # noqa: BLE001 — fall back in-process
            ratelimit.aredis_failed()
    if len(_dash_cache) > 5000:
        _dash_cache.clear()
    _dash_cache[key or oid] = (time.monotonic() + ttl, out)


@router.get("/dashboard")
async def dashboard(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_reports")),
):
    out = await _dash_get(current.org_id)
    if out is None:
        out = await _dashboard(db, current.org_id)
        await _dash_put(current.org_id, out)
    # tk02: the cached tile counts the whole shop's tasks; someone who only sees their own tasks
    # gets their own count (computed per request, never cached).
    vis = await visibility_conds(db, current)
    if vis:
        mine = (await db.execute(select(func.count()).select_from(Task).where(
            Task.org_id == current.org_id, Task.status.notin_(("done", "cancelled")),
            Task.archived_at.is_(None), *vis))).scalar() or 0
        out = {**out, "stats": {**out["stats"], "pending_tasks": mine}}
    return out


async def _dashboard(db, oid: str) -> dict:

    total_orders = (await db.execute(
        select(func.count()).select_from(Order).where(Order.org_id == oid)
    )).scalar() or 0

    open_orders = (await db.execute(
        select(func.count()).select_from(Order)
        .where(Order.org_id == oid, Order.closed_at == None)
    )).scalar() or 0

    total_clients = (await db.execute(
        select(func.count()).select_from(Client).where(Client.org_id == oid)
    )).scalar() or 0

    # net sales to date and what customers still owe (services/revenue)
    revenue = (await money_rules.net_sales(db, oid))["revenue"]
    outstanding = await money_rules.receivables(db, oid)

    pending_tasks = (await db.execute(
        select(func.count()).select_from(Task)
        .where(Task.org_id == oid, Task.status.notin_(("done", "cancelled")), Task.archived_at.is_(None))
    )).scalar() or 0

    # Recent orders
    recent_result = await db.execute(
        select(Order).where(Order.org_id == oid)
        .order_by(Order.created_at.desc()).limit(10)
    )
    recent_orders = recent_result.scalars().all()

    # Orders by status
    status_result = await db.execute(
        select(Order.status, func.count())
        .where(Order.org_id == oid)
        .group_by(Order.status)
    )
    by_status = {row[0]: row[1] for row in status_result.all()}

    low_stock_count = (await db.execute(low_stock_count_query(oid))).scalar() or 0

    return {
        "stats": {
            "total_orders": total_orders,
            "open_orders": open_orders,
            "total_clients": total_clients,
            "revenue": q2(revenue),
            "outstanding": q2(outstanding),
            "pending_tasks": pending_tasks,
            "low_stock_parts": low_stock_count,
        },
        "orders_by_status": by_status,
        "recent_orders": [
            {
                "id": o.id,
                "order_number": o.order_number,
                "status": o.status,
                "priority": o.priority,
                "total": o.total,
                "created_at": o.created_at.isoformat(),
            }
            for o in recent_orders
        ],
    }

@router.get("/orders/by-status")
async def orders_by_status(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    result = await db.execute(
        select(Order.status, func.count())
        .where(Order.org_id == current.org_id)
        .group_by(Order.status)
    )
    return {row[0]: row[1] for row in result.all()}

@router.get("/inventory/low-stock")
async def low_stock_report(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    # The same rule the dashboard and home feed count: active items with a minimum, at or under it.
    rows = (await db.execute(low_stock_query(current.org_id).order_by(Part.name))).all()
    low = [{"id": pid, "name": name, "qty": qty, "min_stock": min_stock}
           for pid, name, min_stock, qty in rows]
    return {"count": len(low), "items": low}


# ════════════════════════════════════════════════════
# Phase C — Reporting, Accounting & Excel
# 20+ reports across 7 tabs. Each report is a parameterised aggregation
# returning {columns, rows}. The same builder feeds the Excel export.
# Gross profit = Price - Discount - Cost ; Markup % = GrossProfit / Cost * 100
# Sold/Provided reports count only Ready + Closed status groups.
# ════════════════════════════════════════════════════
from datetime import timedelta
from fastapi import Query
from fastapi.responses import StreamingResponse
import io
from backend.models import (
    OrderLine, OrderStatus, Transaction, Account, CashFlowItem,
    StockMovement, Supplier, Payment, EventFeedItem,
)
from backend.services.money import q2
from backend.core.spreadsheet import safe_row


# One end of a report window: services/dates.range_edge, shared with the P&L, the accountant
# export and custom reports so they all read a date-only range as the shop's day (council M12: a
# Kiritimati shop asking for its own today got rows=[]).
_range_edge = range_edge


def _parse_range(date_from, date_to, tz: str | None = None):
    df = report_edge(date_from, tz, end=False) if date_from else now_utc() - timedelta(days=30)
    dt = report_edge(date_to, tz, end=True) if date_to else now_utc() + timedelta(days=1)
    return df, dt


# PERF-12: a report reads, builds and exports every row of its window in one request; the window
# is capped like total-profit's, so one run can't pull a shop's whole history into a worker.
MAX_REPORT_DAYS = 731


def _report_window(date_from, date_to, tz: str | None):
    """The window of a built-in report: date-only values are whole shop days, and a date-only
    "To" now runs to the very end of that day (RPT-06: it stopped a second early, so a row stamped
    in the last second of the day was in neither that day nor the next)."""
    from fastapi import HTTPException
    try:
        df, dt = _parse_range(date_from, date_to, tz)
    except ValueError:
        raise HTTPException(422, "Use dates like 2026-10-24")
    # the span is history read: an open "To" far in the future (…&date_to=2100-01-01) reads no
    # more than up to now, so it is measured to tomorrow; the window itself is left as asked
    if min(dt, now_utc() + timedelta(days=1)) - df > timedelta(days=MAX_REPORT_DAYS):
        raise HTTPException(422, "Pick a period of two years or less")
    return df, dt


def _local(ts, kw, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """A stored instant (naive UTC) printed on the shop's clock (RPT-05: report cells printed UTC
    as if it were local)."""
    if ts is None:
        return "—"
    return utc_to_wall(ts, kw.get("tz")).strftime(fmt)


async def _closed_ready_keys(db, org_id):
    """Status keys in the Ready or Closed groups — the only ones counted in
    Sold/Provided reports (Orderry rule)."""
    rows = (await db.execute(select(OrderStatus).where(
        OrderStatus.org_id == org_id, OrderStatus.status_group.in_(["ready", "closed"])))).scalars().all()
    return {s.key for s in rows}


# ── The report builders. Each returns (columns, rows). ──

def _loc_filter(q, kw):
    """Build 141: order-based reports honour ?location_id="""
    lid = kw.get("location_id")
    return q.where(Order.location_id == lid) if lid else q


async def _rep_work_orders_created(db, org, df, dt, **kw):
    _q = _loc_filter(select(Order).where(
        Order.org_id == org, Order.created_at >= df, Order.created_at <= dt), kw)
    rows = (await db.execute(_q
        .options(selectinload(Order.client), selectinload(Order.assigned_to))
        .order_by(Order.created_at.desc()))).scalars().all()
    cols = ["Order #", "Created", "Client", "Status", "Technician", "Total"]
    data = [[o.order_number, _local(o.created_at, kw),
             o.client.name if o.client else "—", o.status,
             o.assigned_to.name if o.assigned_to else "—", q2(o.total or 0)] for o in rows]
    return cols, data


async def _rep_work_orders_closed(db, org, df, dt, **kw):
    _q = _loc_filter(select(Order).where(
        Order.org_id == org, Order.closed_at != None, Order.closed_at >= df, Order.closed_at <= dt), kw)  # noqa: E711
    if kw.get("hide_zero"):
        _q = _q.where(func.coalesce(Order.total, 0) != 0)
    rows = (await db.execute(_q
        .options(selectinload(Order.client), selectinload(Order.assigned_to))
        .order_by(Order.closed_at.desc()))).scalars().all()
    cols = ["Order #", "Closed", "Client", "Technician", "Total"]
    data = [[o.order_number, _local(o.closed_at, kw),
             o.client.name if o.client else "—",
             o.assigned_to.name if o.assigned_to else "—", q2(o.total or 0)] for o in rows]
    return cols, data


async def _rep_lead_source_revenue(db, org, df, dt, **kw):
    """Which lead sources bring paying customers: new clients, orders and money received in the
    period, grouped by the client's lead source (Settings → Directories → Lead sources). "Paid" is
    money in less money handed back (refunds) in the period."""
    src = func.coalesce(func.nullif(Client.source, ""), "Not recorded")
    new_clients = dict((await db.execute(select(src, func.count()).where(
        Client.org_id == org, Client.created_at >= df, Client.created_at <= dt).group_by(src))).all())
    oq = _loc_filter(select(src, func.count(Order.id), func.coalesce(func.sum(Order.total), 0))
        .join(Client, Client.id == Order.client_id).where(
        Order.org_id == org, Order.is_estimate == False,  # noqa: E712
        Order.created_at >= df, Order.created_at <= dt), kw).group_by(src)
    orders = {k: (n, t) for k, n, t in (await db.execute(oq)).all()}
    # A payment names its client directly or only through its order — attribute either way.
    is_refund = func.coalesce(Payment.kind, "payment") == "refund"
    pq = (select(src, func.coalesce(func.sum(case((is_refund, -func.abs(Payment.amount)), else_=Payment.amount)), 0))
          .select_from(Payment).outerjoin(Order, Order.id == Payment.order_id)
          .join(Client, Client.id == func.coalesce(Payment.client_id, Order.client_id))
          .where(Client.org_id == org, Payment.paid_at >= df, Payment.paid_at <= dt).group_by(src))
    paid = dict((await db.execute(pq)).all())
    keys = sorted(set(new_clients) | set(orders) | set(paid), key=lambda k: -float(paid.get(k, 0) or 0))
    cols = ["Lead source", "New clients", "Work orders", "Order value", "Paid", "Avg paid / new client"]
    data = []
    for k in keys:
        n_new = new_clients.get(k, 0)
        n_ord, val = orders.get(k, (0, 0))
        p = float(paid.get(k, 0) or 0)
        data.append([k, n_new, n_ord, q2(val), q2(p), q2(p / n_new) if n_new else "—"])
    return cols, data


async def _rep_cancellations(db, org, df, dt, **kw):
    """Why jobs get cancelled: work orders that entered a dropped status in the period, by the
    reason captured at cancellation (Settings → Directories → Cancellation reasons)."""
    from backend.models import OrderStatus
    dropped = [k for (k,) in (await db.execute(select(OrderStatus.key).where(
        OrderStatus.org_id == org, OrderStatus.status_group == "dropped"))).all()]
    cols = ["Reason", "Work orders", "Share", "Lost value", "Written off"]
    if not dropped:
        return cols, []
    reason = func.coalesce(func.nullif(Order.cancel_reason, ""), "No reason given")
    q = _loc_filter(select(reason, func.count(), func.coalesce(func.sum(Order.total), 0),
                           func.coalesce(func.sum(Order.written_off), 0)).where(
        Order.org_id == org, Order.status.in_(dropped), Order.is_estimate == False,  # noqa: E712
        func.coalesce(Order.closed_at, Order.created_at) >= df,
        func.coalesce(Order.closed_at, Order.created_at) <= dt), kw).group_by(reason)
    rows = sorted((await db.execute(q)).all(), key=lambda r: -r[1])
    total = sum(r[1] for r in rows) or 1
    return cols, [[r, n, f"{round(n * 100 / total)}%", q2(v), q2(w)] for r, n, v, w in rows]


async def _rep_dropped_jobs(db, org, df, dt, **kw):
    """Dropped jobs: one row per job dropped in the period — what was paid, what was
    written off, and the profit after costs, tax and commission (services/profit)."""
    from backend.services.profit import closed_jobs_query, dropped_keys, job_figures
    cols = ["Job #", "Dropped", "Client", "Technician", "Type", "Status", "Reason", "Job total",
            "Paid and kept (incl. tax)", "Written off", "Tax", "Parts cost", "Labour cost", "Commission", "Profit"]
    dropped = await dropped_keys(db, org)
    if kw.get("status"):
        dropped &= {kw["status"]}
    if not dropped:
        return cols, []
    q = closed_jobs_query(org, df, dt, location_id=kw.get("location_id")).where(Order.status.in_(dropped))
    if kw.get("type_id"):
        q = q.where(Order.type_id == kw["type_id"])
    orders = (await db.execute(q.options(selectinload(Order.client), selectinload(Order.assigned_to))
                               .order_by(Order.closed_at.desc()))).scalars().all()
    figs = await job_figures(db, org, q.with_only_columns(Order.id), dropped)   # sums in SQL, no id list
    from backend.models import WorkOrderType
    tids = {o.type_id for o in orders if o.type_id}
    tname = dict((await db.execute(select(WorkOrderType.id, WorkOrderType.name).where(
        WorkOrderType.id.in_(tids), WorkOrderType.org_id == org))).all()) if tids else {}
    sname = dict((await db.execute(select(OrderStatus.key, OrderStatus.name).where(
        OrderStatus.org_id == org, OrderStatus.key.in_(dropped)))).all())
    data = []
    for o in orders:
        f = figs[o.id]
        data.append([o.order_number, _local(o.closed_at, kw, "%Y-%m-%d"), o.client.name if o.client else "—",
                     o.assigned_to.name if o.assigned_to else "—", tname.get(o.type_id, "—"),
                     sname.get(o.status, o.status), o.cancel_reason or "No reason given", f["total"],
                     f["charged"], f["written_off"], f["tax"], f["cogs"], f["labour_cost"],
                     f["commission"], f["profit"]])
    return cols, data


async def _rep_wo_profit(db, org, df, dt, **kw):
    """Per work order created in the period (open ones are estimated): revenue after tax, cost
    (parts + labour + commission), profit, markup %. Same maths as every profit screen
    (services/profit.job_figures)."""
    from backend.services.profit import job_figures
    # Dropped jobs earned nothing to profit from: they're listed on the Dropped jobs report, and
    # what they kept counts in Total profit (main 28989ec6; test_dropped_jobs).
    dropped = await money_rules.dropped_keys(db, org)
    _q = _loc_filter(select(Order).where(
        Order.org_id == org, Order.is_estimate == False, Order.archived == False,  # noqa: E712
        Order.created_at >= df, Order.created_at <= dt,
        *([Order.status.notin_(dropped)] if dropped else [])), kw)
    orders = (await db.execute(_q.order_by(Order.created_at.desc()))).scalars().all()
    figs = await job_figures(db, org, _q.with_only_columns(Order.id))   # sums in SQL, no id list
    cols = ["Order #", "Revenue", "Cost", "Gross profit", "Markup %"]
    data = []
    for o in orders:
        f = figs[o.id]
        cost = q2(f["cogs"] + f["labour_cost"] + f["commission"])
        markup = round(f["profit"] / cost * 100, 1) if cost else 0
        data.append([o.order_number, f["revenue"], cost, f["profit"], markup])
    return cols, data


async def _rep_technicians(db, org, df, dt, **kw):
    """Per-technician: orders closed, revenue, line-level profit."""
    # a dropped job earned only the money it kept (services/revenue), not its lines
    dropped = await money_rules.dropped_keys(db, org)
    lines = (await db.execute(
        select(OrderLine, Order).join(Order, OrderLine.order_id == Order.id)
        .where(Order.org_id == org, Order.created_at >= df, Order.created_at <= dt,
               Order.is_estimate == False, Order.archived == False,     # noqa: E712
               *([Order.status.notin_(dropped)] if dropped else [])))).all()
    emp = {e.id: e.name for e in (await db.execute(select(Employee).where(Employee.org_id == org))).scalars().all()}
    # 144aw — attribute revenue/cost by the SAME split rule payroll uses (co_technicians →
    # single tech → order assignee), so this report reconciles with the commission ledger.
    from backend.services.payroll import _line_splits
    agg = {}
    for line, order in lines:
        for tid, share in _line_splits(line, fallback=order.assigned_to_id):
            a = agg.setdefault(tid, {"rev": 0, "cost": 0, "n": set()})
            a["rev"] += line_net(line.total, line.tax_rate) * share   # ex tax
            a["cost"] += (line.cost_price or 0) * (line.qty or 0) * share
            a["n"].add(order.id)
    cols = ["Technician", "Orders", "Revenue", "Profit"]
    data = [[emp.get(tid, "—"), len(v["n"]), q2(v["rev"]), q2(v["rev"] - v["cost"])] for tid, v in agg.items()]
    return cols, data


async def _line_sales(db, org, df, dt, kind: str) -> dict:
    """Per item: qty, revenue (ex tax) and cost of the `kind` lines on Ready+Closed jobs created
    in the period, less goods returned to the shelf, plus the same items sold at the counter: ex
    tax, each sale's whole-sale discount shared over its lines, less what came back to the till
    (deleted sales left out). A till line with no name is its own row, never merged with another."""
    from backend.models import ClientReturn, ClientReturnLine, Sale, SaleLine
    keys = await _closed_ready_keys(db, org)
    rows = (await db.execute(
        select(OrderLine, Order).join(Order, OrderLine.order_id == Order.id)
        .where(Order.org_id == org, Order.created_at >= df, Order.created_at <= dt,
               Order.is_estimate == False, Order.archived == False,  # noqa: E712
               OrderLine.kind == kind))).all()
    agg = {}
    by_part = {}
    for line, order in rows:
        if order.status not in keys:
            continue
        a = agg.setdefault(line.description, {"qty": 0, "rev": 0, "cost": 0})
        a["qty"] += line.qty
        a["rev"] += line_net(line.total, line.tax_rate)
        a["cost"] += (line.cost_price or 0) * line.qty
        if line.ref_id and line.qty:
            by_part.setdefault((order.id, line.ref_id), (line, a))
    if kind == "product" and by_part:
        back = (await db.execute(
            select(ClientReturn.order_id, ClientReturnLine.part_id, func.sum(ClientReturnLine.qty))
            .join(ClientReturnLine, ClientReturnLine.return_id == ClientReturn.id)
            .where(ClientReturn.order_id.in_(select(Order.id).where(
                       Order.org_id == org, Order.created_at >= df, Order.created_at <= dt)),
                   ClientReturnLine.restock == True)  # noqa: E712
            .group_by(ClientReturn.order_id, ClientReturnLine.part_id))).all()
        for oid, part_id, qty in back:
            hit = by_part.get((oid, part_id))
            if not hit:
                continue
            line, a = hit
            n = min(float(qty or 0), float(line.qty))
            a["qty"] -= n
            a["rev"] -= line_net(line.total, line.tax_rate) / line.qty * n
            a["cost"] -= (line.cost_price or 0) * n
    sales = (await db.execute(select(Sale).where(
        Sale.org_id == org, Sale.voided_at.is_(None), Sale.created_at >= df, Sale.created_at <= dt))).scalars().all()
    if sales:
        # the period's sales as a SELECT, not a Python list (PERF-15: asyncpg takes ≤ 32,767 binds)
        slines = (await db.execute(select(SaleLine).where(SaleLine.sale_id.in_(select(Sale.id).where(
            Sale.org_id == org, Sale.voided_at.is_(None), Sale.created_at >= df, Sale.created_at <= dt))))).scalars().all()
        gross = {}
        for sl in slines:
            gross[sl.sale_id] = gross.get(sl.sale_id, 0) + float(sl.total or 0)
        # what the customer paid ex tax (Sale.tax holds the tax, added on top or inside the price)
        net = {x.id: float(x.total or 0) - float(getattr(x, "tax", 0) or 0) for x in sales}
        numbers = {x.id: x.sale_number for x in sales}
        for sl in slines:
            if sl.kind != kind or not sl.qty:
                continue
            share = net[sl.sale_id] / gross[sl.sale_id] if gross.get(sl.sale_id) else 0
            # units brought back to the till (sales.return_sale_items) were not sold
            kept = max(0.0, float(sl.qty) - float((sl.stock_draws or {}).get("returned") or 0))
            name = (sl.description or "").strip()
            key = name or ("unnamed", sl.id)
            a = agg.setdefault(key, {"qty": 0, "rev": 0, "cost": 0,
                                     "name": name or f"Unnamed item ({numbers[sl.sale_id] or 'counter sale'})"})
            a["qty"] += kept
            a["rev"] += float(sl.total or 0) * share * kept / float(sl.qty)
            a["cost"] += float(sl.cost_price or 0) * kept
    return agg


def _profit_rows(agg):
    data = []
    for name, v in agg.items():
        gp = v["rev"] - v["cost"]
        data.append([v.get("name", name), q2(v["qty"]), q2(v["rev"]), q2(v["cost"]),
                     q2(gp), round(gp / v["cost"] * 100, 1) if v["cost"] else 0])
    return data


async def _rep_provided_services(db, org, df, dt, **kw):
    """Services on Ready+Closed orders and at the counter. Ex tax. Gross profit + markup."""
    cols = ["Service", "Qty", "Revenue", "Cost", "Gross profit", "Markup %"]
    return cols, _profit_rows(await _line_sales(db, org, df, dt, "service"))


async def _rep_sold_products(db, org, df, dt, **kw):
    """Products on Ready+Closed orders (less returns) and at the counter. Ex tax."""
    cols = ["Product", "Qty sold", "Revenue", "Cost", "Gross profit", "Markup %"]
    return cols, _profit_rows(await _line_sales(db, org, df, dt, "product"))


async def _rep_cash_flow(db, org, df, dt, **kw):
    """Cash flow statement — income vs expense by cash-flow item. A deleted transaction (a voided
    sale's takings, a deleted refund's payout) is struck through and left out, as in the account."""
    txns = (await db.execute(select(Transaction).where(
        Transaction.org_id == org, func.coalesce(Transaction.status, "active") != "deleted",
        Transaction.actual_date >= df, Transaction.actual_date <= dt))).scalars().all()
    items = {c.id: c.name for c in (await db.execute(select(CashFlowItem).where(CashFlowItem.org_id == org))).scalars().all()}
    agg = {}
    for t in txns:
        # money handed back to a customer is a refund, even where it was posted as an expense
        kind = "refund" if t.txn_type == "expense" and t.ref_type in money_rules.CUSTOMER_REF_TYPES else t.txn_type
        name = items.get(t.cash_flow_item_id, kind)
        agg[name] = agg.get(name, 0) + t.amount
    cols = ["Cash flow item", "Net amount"]
    data = [[k, q2(v)] for k, v in sorted(agg.items(), key=lambda x: -x[1])]
    return cols, data


async def _rep_total_money(db, org, df, dt, **kw):
    """Account balances snapshot."""
    accts = (await db.execute(select(Account).where(Account.org_id == org, Account.active == True))).scalars().all()  # noqa: E712
    cols = ["Account", "Type", "Balance"]
    data = [[a.name, a.payment_option, q2(a.balance)] for a in accts]
    return cols, data


# "return" movements that undo a use: parts back from a job, a deleted sale, a customer's return
_USE_UNDONE_BY = ("order", "sale_void", "client_return")


def _unit_cost(p) -> float:
    """The one cost basis for stock value (moving average, else list cost), as on the product card."""
    from backend.services.valuation import unit_cost
    return unit_cost(p)


async def _rep_product_turnover(db, org, df, dt, **kw):
    """Goods written off to orders/sales (movement_type=use), less what came back in the period:
    a line removed from a job, a deleted sale, goods returned by the customer. `movement`: "with"
    (default) lists products used in the period; "without" lists active products with no use in
    it; "all" both."""
    movement = kw.get("movement") or "with"
    rows = (await db.execute(
        select(StockMovement, Part).options(_NO_PHOTO).join(Part, StockMovement.part_id == Part.id)
        .where(Part.org_id == org, StockMovement.movement_type.in_(("use", "return")),
               StockMovement.created_at >= df, StockMovement.created_at <= dt))).all()
    agg: dict = {}
    for mv, part in rows:
        if mv.movement_type == "return" and mv.ref_type not in _USE_UNDONE_BY:
            continue
        a = agg.setdefault(part.id, [part.name, part.sku or "—", 0.0])
        a[2] += abs(mv.qty or 0) if mv.movement_type == "use" else -abs(mv.qty or 0)
    agg = {k: v for k, v in agg.items() if v[2] > 1e-9}
    cols = ["Product", "SKU", "Qty used (orders/sales)"]
    data = [[n, sku, q2(v)] for n, sku, v in agg.values()] if movement in ("with", "all") else []
    if movement in ("without", "all"):
        idle = (await db.execute(select(Part.name, Part.sku).where(
            Part.org_id == org, Part.active == True,  # noqa: E712
            Part.id.notin_(list(agg) or [""])).order_by(Part.name))).all()
        data += [[n, sku or "—", 0] for n, sku in idle]
    return cols, data


async def _rep_inventory_writeoffs(db, org, df, dt, **kw):
    """Manual write-offs (movement_type=writeoff) — separate report from turnover."""
    rows = (await db.execute(
        select(StockMovement, Part).options(_NO_PHOTO).join(Part, StockMovement.part_id == Part.id)
        .where(Part.org_id == org, StockMovement.movement_type == "writeoff",
               StockMovement.created_at >= df, StockMovement.created_at <= dt))).all()
    cols = ["Product", "Qty", "Date"]
    data = [[part.name, abs(mv.qty), _local(mv.created_at, kw, "%Y-%m-%d")] for mv, part in rows]
    return cols, data


async def _rep_stock_levels(db, org, df, dt, **kw):
    parts = (await db.execute(select(Part).where(Part.org_id == org, Part.active == True)  # noqa: E712
        .options(selectinload(Part.stock_levels), _NO_PHOTO))).scalars().all()
    cols = ["Product", "SKU", "Available", "Reserved", "Min", "Value (cost)"]
    data = []
    for p in parts:
        avail = sum(s.qty_available for s in p.stock_levels)
        res = sum(s.qty_reserved for s in p.stock_levels)
        # valued like stock_valuation / assortment: what is on hand, at the one cost basis
        value = sum(max(s.qty_available or 0, 0) for s in p.stock_levels) * _unit_cost(p)
        data.append([p.name, p.sku or "—", q2(avail), q2(res), p.min_stock, q2(value)])
    return cols, data


async def _rep_activity_log(db, org, df, dt, **kw):
    # Text only: the row's photo or file never leaves the database for this report (PERF-05).
    rows = (await db.execute(select(
        EventFeedItem.created_at, EventFeedItem.entity_type, EventFeedItem.body).where(
        EventFeedItem.org_id == org, EventFeedItem.created_at >= df, EventFeedItem.created_at <= dt)
        .order_by(EventFeedItem.created_at.desc()).limit(500))).all()
    cols = ["When", "Entity", "Event"]
    data = [[_local(r.created_at, kw), r.entity_type or "—", r.body or ""] for r in rows]
    return cols, data


async def _rep_labor_utilization(db, org, df, dt, **kw):
    """Per-technician clocked vs billable hours and utilization in the window."""
    from backend.models import TimeEntry
    rows = (await db.execute(select(TimeEntry).where(
        TimeEntry.org_id == org, TimeEntry.state != "void",
        TimeEntry.started_at >= df, TimeEntry.started_at < dt))).scalars().all()
    scope = kw.get("payroll_ids")          # E3: labour cost reveals pay rates
    if scope is not None:
        rows = [e for e in rows if e.employee_id in scope]
    emps = {e.id: e.name for e in (await db.execute(
        select(Employee).where(Employee.org_id == org))).scalars().all()}
    agg = {}
    for e in rows:
        a = agg.setdefault(e.employee_id, {"clocked": 0, "billable": 0, "job": 0, "cost": 0.0})
        a["clocked"] += e.duration_sec
        if e.billable:
            a["billable"] += e.duration_sec
        if e.order_id:
            a["job"] += e.duration_sec
        a["cost"] += e.duration_sec / 3600 * (e.cost_rate or 0)
    cols = ["Technician", "Clocked h", "Billable h", "Job h", "Utilization %", "Labor cost"]
    out = []
    for eid, a in agg.items():
        util = round(a["billable"] / a["clocked"] * 100, 1) if a["clocked"] else 0
        out.append([emps.get(eid, eid), q2(a["clocked"] / 3600), q2(a["billable"] / 3600),
                    q2(a["job"] / 3600), util, q2(a["cost"])])
    out.sort(key=lambda r: -r[1])
    return cols, out


async def _rep_labor_by_job(db, org, df, dt, **kw):
    """Tracked labor per work order: scheduled vs tracked vs billed."""
    from backend.models import TimeEntry
    rows = (await db.execute(select(TimeEntry).where(
        TimeEntry.org_id == org, TimeEntry.state != "void", TimeEntry.order_id.is_not(None),
        TimeEntry.started_at >= df, TimeEntry.started_at < dt))).scalars().all()
    scope = kw.get("payroll_ids")          # E3: labour cost reveals pay rates
    if scope is not None:
        rows = [e for e in rows if e.employee_id in scope]
    by_order = {}
    for e in rows:
        a = by_order.setdefault(e.order_id, {"sec": 0, "cost": 0.0, "bill": 0.0})
        a["sec"] += e.duration_sec
        a["cost"] += e.duration_sec / 3600 * (e.cost_rate or 0)
        if e.billable:
            a["bill"] += e.duration_sec / 3600 * (e.billable_rate or 0)
    order_rows = [o for part in money_rules.chunks(by_order) for o in (await db.execute(select(Order).where(
        Order.org_id == org, Order.id.in_(part)))).scalars().all()]      # PERF-15: chunked
    onum = {o.id: (o.order_number, o.duration_min or 0, o.labour_total or 0) for o in order_rows}
    cols = ["Order", "Scheduled min", "Tracked h", "Labor cost", "Billable value", "Billed labor"]
    out = []
    for oid, a in by_order.items():
        num, sched, billed = onum.get(oid, (oid, 0, 0))
        out.append([num, sched, q2(a["sec"] / 3600), q2(a["cost"]),
                    q2(a["bill"]), q2(billed)])
    out.sort(key=lambda r: -r[2])
    return cols, out


async def _rep_labor_efficiency(db, org, df, dt, **kw):
    """144ax — per-technician BOOKED vs ACTUAL hours and efficiency. Booked = each line's
    booked_hours credited by the same split rule payroll uses; Actual = measured JOB clocked
    hours by the employee who worked (not allocated). Efficiency = booked ÷ actual × 100;
    over 100% means the tech beat the estimate. This is the aggregate behind the inline delta."""
    from backend.models import TimeEntry
    from backend.services.payroll import _line_splits
    from backend.services.labour import entry_seconds, is_job_actual
    emps = {e.id: e.name for e in (await db.execute(
        select(Employee).where(Employee.org_id == org))).scalars().all()}
    # Booked hours credited per technician (co_technicians → single tech → order assignee).
    lines = (await db.execute(
        select(OrderLine, Order).join(Order, OrderLine.order_id == Order.id)
        .where(Order.org_id == org, Order.created_at >= df, Order.created_at < dt))).all()
    booked: dict = {}
    for line, order in lines:
        bh = float(getattr(line, "booked_hours", 0) or 0)
        if bh <= 0:
            continue
        for tid, share in _line_splits(line, fallback=order.assigned_to_id):
            booked[tid] = booked.get(tid, 0.0) + bh * share
    # Actual measured hours per technician (who actually clocked the job time).
    te = (await db.execute(select(TimeEntry).where(
        TimeEntry.org_id == org, TimeEntry.started_at >= df, TimeEntry.started_at < dt))).scalars().all()
    actual: dict = {}
    for e in te:
        if is_job_actual(e):
            actual[e.employee_id] = actual.get(e.employee_id, 0.0) + entry_seconds(e) / 3600
    cols = ["Technician", "Booked h", "Actual h", "Variance h", "Efficiency %"]
    out = []
    for tid in set(list(booked.keys()) + list(actual.keys())):
        b, a = q2(booked.get(tid, 0.0)), q2(actual.get(tid, 0.0))
        eff = round(b / a * 100) if a else 0
        out.append([emps.get(tid, "—"), b, a, q2(b - a), eff])
    out.sort(key=lambda r: -r[1])
    return cols, out


async def _rep_labor_cost(db, org, df, dt, **kw):
    """Org-level labor totals + overtime in the window."""
    from backend.models import TimeEntry
    rows = (await db.execute(select(TimeEntry).where(
        TimeEntry.org_id == org, TimeEntry.state != "void",
        TimeEntry.started_at >= df, TimeEntry.started_at < dt))).scalars().all()
    scope = kw.get("payroll_ids")          # E3: only the people the caller's payroll scope covers
    if scope is not None:
        rows = [e for e in rows if e.employee_id in scope]
    total = sum(e.duration_sec for e in rows)
    billable = sum(e.duration_sec for e in rows if e.billable)
    cost = sum(e.duration_sec / 3600 * (e.cost_rate or 0) for e in rows)
    bill = sum(e.duration_sec / 3600 * (e.billable_rate or 0) for e in rows if e.billable)
    # Overtime is per person per shop day (RPT-01), the same rule the timesheet pays by, on the same
    # clock payroll counts each person's days on: their branch's zone, else the shop's (N-11; this
    # used the report location's zone for everyone).
    from backend.models import Employee
    from backend.routers.timeclock import _overtime_split
    per_emp: dict = {}
    for e in rows:
        per_emp.setdefault(e.employee_id, []).append(e)
    homes = dict((await db.execute(select(Employee.id, Employee.location_id).where(
        Employee.org_id == org, Employee.id.in_(list(per_emp))))).all()) if per_emp else {}
    zones: dict = {}
    for loc in set(homes.values()) | {None}:
        zones[loc] = await shop_tzname(db, org, loc)
    ot = sum(_overtime_split(es, zones[homes.get(emp_id)])[1] for emp_id, es in per_emp.items())
    cols = ["Metric", "Value"]
    out = [["Total hours", q2(total / 3600)], ["Billable hours", q2(billable / 3600)],
           ["Overtime hours (>8h/day)", q2(ot)], ["Labor cost", q2(cost)],
           ["Billable value", q2(bill)],
           ["Utilization %", round(billable / total * 100, 1) if total else 0]]
    return cols, out


# Report registry: key -> (tab, title, builder)

# ── Build 96: warehouse reports ──────────────────────────────────────────────
async def _rep_assortment(db, org, df, dt, **kw):
    """What we stock, where it sits, and what it's worth — the warehouse-by-warehouse
    view that "stock levels" flattens away. Valued at moving-average cost, since that's
    what the goods actually cost us, not what we hoped to pay."""
    from backend.models import Warehouse
    whs = {w.id: w for w in (await db.execute(select(Warehouse).where(
        Warehouse.org_id == org, Warehouse.kind == "product",
        Warehouse.deleted_at.is_(None)))).scalars().all()}
    rows = (await db.execute(select(StockLevel, Part).options(_NO_PHOTO)
        .join(Part, StockLevel.part_id == Part.id)
        .where(Part.org_id == org, Part.active == True))).all()  # noqa: E712

    cols = ["Warehouse", "Product", "SKU", "Category", "Available", "Reserved",
            "Unit cost", "Value"]
    data = []
    for sl, p in rows:
        avail = sl.qty_available or 0
        if not avail and not (sl.qty_reserved or 0):
            continue
        w = whs.get(sl.warehouse_id)
        cost = _unit_cost(p)
        data.append([w.name if w else "—", p.name, p.sku or "—", p.category or "—",
                     q2(avail), q2(sl.qty_reserved or 0),
                     q2(cost), q2(max(avail, 0) * cost)])
    data.sort(key=lambda r: (r[0], -r[7]))
    return cols, data


async def _on_order(db, org) -> dict:
    """{part id: units still to arrive} over the shop's open purchase orders, in one grouped
    query (PERF-11: it used to be one query per open PO). Only lines with something still
    outstanding count, as before."""
    from backend.models import POLine, PurchaseOrder
    out = func.coalesce(POLine.qty_ordered, 0) - func.coalesce(POLine.qty_received, 0)
    open_pos = (select(PurchaseOrder.id)
                .join(Supplier, PurchaseOrder.supplier_id == Supplier.id)
                .where(Supplier.org_id == org,
                       PurchaseOrder.status.notin_(["received", "canceled", "cancelled"])))
    return {pid: float(q or 0) for pid, q in (await db.execute(
        select(POLine.part_id, func.sum(out))
        .where(POLine.po_id.in_(open_pos), out > 0)
        .group_by(POLine.part_id))).all()}


async def _rep_purchase_list(db, org, df, dt, **kw):
    """What to buy. A product is short when what's on the shelf, minus what's already
    promised to a customer, falls below its minimum — reserved stock isn't really yours
    to sell. Anything already on order is netted off, so the same part isn't ordered
    twice while the first delivery is still in transit."""

    parts = (await db.execute(select(Part).where(
        Part.org_id == org, Part.active == True)  # noqa: E712
        .options(selectinload(Part.stock_levels), _NO_PHOTO))).scalars().all()

    # what's already coming
    incoming = await _on_order(db, org)

    cols = ["Product", "SKU", "Available", "Reserved", "Free", "Min", "On order",
            "Suggest order", "Est. cost"]
    data = []
    for p in parts:
        avail = sum(s.qty_available or 0 for s in p.stock_levels)
        res = sum(s.qty_reserved or 0 for s in p.stock_levels)
        free = avail - res
        min_stock = p.min_stock or 0
        on_order = incoming.get(p.id, 0)
        shortfall = min_stock - free - on_order
        if shortfall <= 0:
            continue
        cost = _unit_cost(p)
        data.append([p.name, p.sku or "—", q2(avail), q2(res), q2(free), min_stock, q2(on_order),
                     q2(shortfall), q2(shortfall * cost)])
    data.sort(key=lambda r: -r[8])
    return cols, data


async def _rep_expiration(db, org, df, dt, **kw):
    """Batches by how close they are to expiring. Anything already past its date is
    reported as a negative day count rather than hidden — expired stock sitting on a
    shelf is exactly the thing this report exists to surface."""

    from backend.models import StockItemBatch, Warehouse
    whs = {w.id: w.name for w in (await db.execute(select(Warehouse).where(
        Warehouse.org_id == org))).scalars().all()}
    rows = (await db.execute(select(StockItemBatch, Part).options(_NO_PHOTO)
        .join(Part, StockItemBatch.part_id == Part.id)
        .where(StockItemBatch.org_id == org,
               StockItemBatch.expiry_date.isnot(None),
               StockItemBatch.qty_remaining > 0))).all()

    today = await shop_today(db, org)
    cols = ["Product", "SKU", "Warehouse", "Qty left", "Expires", "Days left", "Status",
            "Value at risk"]
    data = []
    for b, p in rows:
        exp = b.expiry_date.date() if hasattr(b.expiry_date, "date") else b.expiry_date
        days = (exp - today).days
        status = "expired" if days < 0 else "expiring soon" if days <= 30 else "ok"
        cost = float(b.unit_cost or p.avg_cost or p.cost_price or 0)
        data.append([p.name, p.sku or "—", whs.get(b.warehouse_id, "—"),
                     q2(b.qty_remaining or 0), exp.isoformat(), days, status,
                     q2(float(b.qty_remaining or 0) * cost)])
    data.sort(key=lambda r: r[5])     # soonest (and most overdue) first
    return cols, data


async def _rep_payroll(db, org, df, dt, **kw):
    """Per-employee compensation accrued in the window, straight from the payroll
    ledger: commissions, hourly wages (both the labour-hours and shop-attendance
    clocks post here), manual bonuses/penalties, and accrued base salary.
    E2: narrowed by location, role (position) and employee; `payroll_ids` is the caller's
    payroll scope (None = everyone)."""
    from backend.models import EmployeeBalanceLedger, Role
    q = select(Employee).where(Employee.org_id == org, Employee.active == True)  # noqa: E712
    if kw.get("location_id"):
        # no home location = not tied to a branch, so they count in every branch's report
        q = q.where((Employee.location_id == kw["location_id"]) | Employee.location_id.is_(None))
    if kw.get("role_id"):
        q = q.where(Employee.role_id == kw["role_id"])
    if kw.get("employee_id"):
        q = q.where(Employee.id == kw["employee_id"])
    scope = kw.get("payroll_ids")
    emps = [e for e in (await db.execute(q)).scalars().all() if scope is None or e.id in scope]
    roles = {r.id: r.name for r in (await db.execute(
        select(Role).where(Role.org_id == org))).scalars().all()}
    led = (await db.execute(select(EmployeeBalanceLedger).where(
        EmployeeBalanceLedger.org_id == org,
        EmployeeBalanceLedger.created_at >= df,
        EmployeeBalanceLedger.created_at < dt))).scalars().all()
    agg = {}
    for e in led:
        a = agg.setdefault(e.employee_id, {"commission": 0.0, "hourly_wage": 0.0,
                                           "bonus": 0.0, "penalty": 0.0, "base": 0.0})
        amt = float(e.amount or 0)
        if e.entry_type in ("commission", "clawback"):
            a["commission"] += amt
        elif e.entry_type == "hourly_wage":
            a["hourly_wage"] += amt
        elif e.entry_type == "bonus":
            a["bonus"] += amt
        elif e.entry_type == "penalty":
            a["penalty"] += amt          # stored negative
        elif e.entry_type == "base":
            a["base"] += amt
    cols = ["Employee", "Position", "Commission", "Hourly", "Bonuses", "Penalties", "Base", "Total"]
    out = []
    for e in sorted(emps, key=lambda x: (x.name or "").lower()):
        a = agg.get(e.id, {"commission": 0.0, "hourly_wage": 0.0, "bonus": 0.0, "penalty": 0.0, "base": 0.0})
        penalty = abs(a["penalty"])
        total = a["commission"] + a["hourly_wage"] + a["bonus"] - penalty + a["base"]
        out.append([e.name, roles.get(e.role_id) or "—",
                    q2(a["commission"]), q2(a["hourly_wage"]),
                    q2(a["bonus"]), q2(penalty), q2(a["base"]), q2(total)])
    return cols, out


async def _rep_stock_valuation(db, org, df, dt, **kw):
    """The accountant's number: total on-hand stock value at moving-average cost,
    subtotalled per warehouse with a grand total. `assortment` lists every line;
    this rolls it up to what the whole inventory is worth right now."""
    from backend.models import Warehouse
    whs = {w.id: w.name for w in (await db.execute(select(Warehouse).where(
        Warehouse.org_id == org, Warehouse.kind == "product",
        Warehouse.deleted_at.is_(None)))).scalars().all()}
    rows = (await db.execute(select(StockLevel, Part).options(_NO_PHOTO)
        .join(Part, StockLevel.part_id == Part.id)
        .where(Part.org_id == org, Part.active == True))).all()  # noqa: E712
    agg = {}
    for sl, p in rows:
        avail = sl.qty_available or 0
        if avail <= 0:
            continue
        cost = _unit_cost(p)
        a = agg.setdefault(sl.warehouse_id, {"skus": set(), "units": 0.0, "value": 0.0})
        a["skus"].add(p.id)
        a["units"] += avail
        a["value"] += avail * cost
    cols = ["Warehouse", "Distinct SKUs", "Units on hand", "Stock value (cost)"]
    data = [[whs.get(wid, "—"), len(a["skus"]), q2(a["units"]), q2(a["value"])] for wid, a in agg.items()]
    data.sort(key=lambda r: -r[3])
    if data:
        data.append(["TOTAL", sum(r[1] for r in data), q2(sum(r[2] for r in data)),
                     q2(sum(r[3] for r in data))])
    return cols, data


async def _rep_dead_stock(db, org, df, dt, **kw):
    """Money sitting still. On-hand parts with no sale/use in 90+ days (or never sold) —
    the tied-up cash you could free by clearing them. The inverse of the reorder list."""
    parts = (await db.execute(select(Part).where(
        Part.org_id == org, Part.active == True)  # noqa: E712
        .options(selectinload(Part.stock_levels), _NO_PHOTO))).scalars().all()
    last = {}
    mv_rows = (await db.execute(
        select(StockMovement.part_id, func.max(StockMovement.created_at))
        .join(Part, StockMovement.part_id == Part.id)
        .where(Part.org_id == org, StockMovement.movement_type == "use")
        .group_by(StockMovement.part_id))).all()
    for pid, ts in mv_rows:
        last[pid] = ts
    now = now_utc()
    cols = ["Product", "SKU", "On hand", "Unit cost", "Tied-up cash", "Last sold", "Days idle"]
    data = []
    for p in parts:
        avail = sum(s.qty_available or 0 for s in p.stock_levels)
        if avail <= 0:
            continue
        ts = last.get(p.id)
        days = (now - ts).days if ts else None
        if days is not None and days < 90:
            continue
        cost = _unit_cost(p)
        data.append([p.name, p.sku or "—", q2(avail), q2(cost), q2(avail * cost), _local(ts, kw, "%Y-%m-%d") if ts else "never",
                     days if days is not None else "—"])
    data.sort(key=lambda r: -r[4])
    return cols, data


async def _rep_reorder_forecast(db, org, df, dt, **kw):
    """Velocity-based reordering. From 28-day sales/use, projects each moving part's
    days-of-cover and stockout date, then suggests an order quantity that covers the
    supplier's lead time plus a 14-day buffer — netting off what's already on order.
    Unlike the min-stock purchase list, this reacts to how fast things actually move."""
    WINDOW, COVER = 28, 14
    since = now_utc() - timedelta(days=WINDOW)
    vel = {pid: float(q or 0) for pid, q in (await db.execute(
        select(StockMovement.part_id, func.sum(func.abs(StockMovement.qty)))
        .join(Part, StockMovement.part_id == Part.id)
        .where(Part.org_id == org, StockMovement.movement_type == "use",
               StockMovement.created_at >= since)
        .group_by(StockMovement.part_id))).all()}
    incoming = await _on_order(db, org)
    lead = {s.id: (s.lead_days or 0) for s in (await db.execute(
        select(Supplier).where(Supplier.org_id == org))).scalars().all()}
    parts = (await db.execute(select(Part).where(
        Part.org_id == org, Part.active == True)  # noqa: E712
        .options(selectinload(Part.stock_levels), _NO_PHOTO))).scalars().all()
    cols = ["Product", "SKU", "Free", "Daily use", "Days left", "Stockout ~", "Lead days",
            "On order", "Suggest order", "Est. cost"]
    data = []
    for p in parts:
        daily = vel.get(p.id, 0) / WINDOW
        if daily <= 0:
            continue
        avail = sum(s.qty_available or 0 for s in p.stock_levels)
        res = sum(s.qty_reserved or 0 for s in p.stock_levels)
        free = avail - res
        ld = lead.get(p.default_supplier_id, 0)
        days_left = free / daily
        suggest = daily * (ld + COVER) - free - incoming.get(p.id, 0)
        if suggest <= 0:
            continue
        cost = _unit_cost(p)
        stockout = (now_utc() + timedelta(days=days_left)).strftime("%Y-%m-%d")
        data.append([p.name, p.sku or "—", q2(free), q2(daily), round(days_left, 1), stockout, ld, q2(incoming.get(p.id, 0)),
                     stock_rows.order_qty(p, suggest), q2(stock_rows.order_qty(p, suggest) * cost)])
    data.sort(key=lambda r: r[4])
    return cols, data


# Reports exposing sensitive figures need the matching permission on top of can_view_reports.
REPORT_PERMS = {
    "payroll": "can_view_payroll", "labor_cost": "can_view_payroll",
    "labor_utilization": "can_view_payroll", "labor_by_job": "can_view_payroll",   # labour cost = pay rates
    "wo_profit": ("can_see_profit", "can_view_purchase_price"),   # plain table: its cost column can't be hidden
    # A plain table can't have its cost columns removed per viewer, so it needs both rights.
    "dropped_jobs": ("can_see_profit", "can_view_purchase_price"),
    "cash_flow": "can_view_cashflow", "total_money": "can_view_balance",
    "stock_valuation": "can_view_purchase_price",
}


# Downloading these as a file needs its own right on top of seeing them.
EXPORT_PERMS = {
    "sold_products": "can_export_sales", "provided_services": "can_export_sales",
    "payroll": "can_export_payroll", "labor_cost": "can_export_payroll",
    "labor_utilization": "can_export_payroll", "labor_by_job": "can_export_payroll",
}


async def _assert_report_access(db, current, report_key: str, export: bool = False) -> None:
    from backend.services.auth import assert_perm
    await assert_perm(db, current, "can_view_reports")
    need = REPORT_PERMS.get(report_key) or ()
    for perm in ((need,) if isinstance(need, str) else need):
        await assert_perm(db, current, perm)
    if export and report_key in EXPORT_PERMS:
        await assert_perm(db, current, EXPORT_PERMS[report_key])


PAYROLL_REPORTS = {"payroll", "labor_cost", "labor_utilization", "labor_by_job"}


class ReportFilters:
    """Optional report filters; each builder reads only the ones it understands, unknown values
    are dropped. movement: product turnover (with | without | all); hide_zero: closed jobs;
    type_id / status: unsuccessful jobs."""
    def __init__(self, movement: Optional[str] = Query(None), hide_zero: Optional[str] = Query(None, max_length=10),
                 type_id: Optional[str] = Query(None, max_length=64),
                 status: Optional[str] = Query(None, max_length=60)):
        self.kw = {k: v for k, v in {
            "movement": movement if movement in ("with", "without", "all") else None,
            "hide_zero": (hide_zero or "").lower() in ("1", "true", "yes", "on"),
            "type_id": type_id, "status": status}.items() if v}


async def _builder_kw(db, current, report_key: str, location_id, role_id, employee_id,
                      filters: Optional[ReportFilters] = None) -> dict:
    """Extra builder arguments: the branch, plus (payroll reports) the role/employee filters and
    the caller's payroll scope, so the screen, the run and every export show the same rows."""
    kw = {"location_id": location_id, **(filters.kw if filters else {})}
    if report_key in PAYROLL_REPORTS:
        from backend.services.payroll_views import assert_payroll_scope, payroll_scope_ids
        if employee_id:
            await assert_payroll_scope(db, current, employee_id)
        kw.update(role_id=role_id, employee_id=employee_id,
                  payroll_ids=await payroll_scope_ids(db, current))
    return kw


async def _report_from(db, current, report_key: str, df):
    """The cash flow starts no earlier than today for a role without "any period"."""
    if report_key != "cash_flow":
        return df
    from backend.routers.cash import cash_flow_floor
    since = await cash_flow_floor(db, current)
    return max(df, since) if since else df


REPORTS = {
    "payroll":          ("Finance", "Payroll summary", _rep_payroll),
    "wo_created":       ("Work Orders", "Work orders created", _rep_work_orders_created),
    "labor_utilization":("Labor", "Technician utilization", _rep_labor_utilization),
    "labor_efficiency": ("Labor", "Labor efficiency (booked vs actual)", _rep_labor_efficiency),
    "labor_by_job":     ("Labor", "Labor by work order", _rep_labor_by_job),
    "labor_cost":       ("Labor", "Labor cost summary", _rep_labor_cost),
    "wo_closed":        ("Work Orders", "Work orders closed", _rep_work_orders_closed),
    "wo_profit":        ("Finance", "Work orders profit", _rep_wo_profit),
    "lead_sources":     ("Customers", "Revenue by lead source", _rep_lead_source_revenue),
    "cancellations":    ("Work Orders", "Cancellations by reason", _rep_cancellations),
    "dropped_jobs":     ("Finance", "Dropped jobs", _rep_dropped_jobs),
    "technicians":      ("Work Orders", "Technicians performance", _rep_technicians),
    "provided_services":("Assortment", "Provided services (Ready+Closed)", _rep_provided_services),
    "sold_products":    ("Assortment", "Sold products (Ready+Closed)", _rep_sold_products),
    "cash_flow":        ("Finance", "Cash flow statement", _rep_cash_flow),
    "total_money":      ("Finance", "Total money (accounts)", _rep_total_money),
    "product_turnover": ("Inventory", "Product turnover", _rep_product_turnover),
    "inventory_writeoffs":("Inventory", "Inventory write-offs", _rep_inventory_writeoffs),
    "stock_levels":     ("Inventory", "Stock levels", _rep_stock_levels),
    "stock_valuation":  ("Inventory", "Stock valuation (by warehouse)", _rep_stock_valuation),
    "assortment":       ("Inventory", "Assortment by warehouse", _rep_assortment),
    "purchase_list":    ("Inventory", "Purchase list (what to buy)", _rep_purchase_list),
    "reorder_forecast": ("Inventory", "Reorder forecast (velocity)", _rep_reorder_forecast),
    "dead_stock":       ("Inventory", "Dead & slow stock", _rep_dead_stock),
    "expiration":       ("Inventory", "Expiring stock", _rep_expiration),
    "activity_log":     ("Activity Log", "Activity log", _rep_activity_log),
}


@router.get("/catalog")
async def report_catalog(current: Employee = Depends(get_current_employee)):
    """List available reports grouped by tab."""
    tabs = {}
    for key, (tab, title, _) in REPORTS.items():
        tabs.setdefault(tab, []).append({"key": key, "title": title})
    return {"tabs": [{"name": t, "reports": r} for t, r in tabs.items()]}


@router.get("/run/{report_key}")
async def run_report(
    report_key: str,
    date_from: str = Query(None),
    date_to: str = Query(None),
    location_id: str = Query(None),           # build 141: scope order-based reports to a branch
    role_id: str = Query(None),               # E2: payroll reports — one role (position)
    employee_id: str = Query(None),           # E2: payroll reports — one employee
    filters: ReportFilters = Depends(),
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    await _assert_report_access(db, current, report_key)
    if report_key not in REPORTS:
        from fastapi import HTTPException
        raise HTTPException(404, "Unknown report")
    tab, title, builder = REPORTS[report_key]
    location_id = await _scoped_location(db, current, location_id)
    tz = await shop_tzname(db, current.org_id, location_id)
    df, dt = _report_window(date_from, date_to, tz)
    df = await _report_from(db, current, report_key, df)
    cols, rows = await builder(db, current.org_id, df, dt, tz=tz,
                               **await _builder_kw(db, current, report_key, location_id, role_id, employee_id, filters))
    # totals row for numeric columns
    return {"key": report_key, "title": title, "tab": tab,
            "columns": cols, "rows": rows, "row_count": len(rows)}


@router.get("/export/{report_key}")
async def export_report(
    report_key: str,
    date_from: str = Query(None),
    date_to: str = Query(None),
    location_id: str = Query(None),
    role_id: str = Query(None),
    employee_id: str = Query(None),
    filters: ReportFilters = Depends(),
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Stream the report as a styled .xlsx (Orderry: export carries the full data)."""
    await _assert_report_access(db, current, report_key, export=True)
    if report_key not in REPORTS:
        from fastapi import HTTPException
        raise HTTPException(404, "Unknown report")
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    tab, title, builder = REPORTS[report_key]
    location_id = await _scoped_location(db, current, location_id)
    tz = await shop_tzname(db, current.org_id, location_id)
    df, dt = _report_window(date_from, date_to, tz)
    df = await _report_from(db, current, report_key, df)
    cols, rows = await builder(db, current.org_id, df, dt, tz=tz,
                               **await _builder_kw(db, current, report_key, location_id, role_id, employee_id, filters))

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = title[:31]
    head_fill = PatternFill("solid", fgColor="4F8EF7")
    head_font = Font(bold=True, color="FFFFFF")
    ws.append([title])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([f"{df.strftime('%Y-%m-%d')} → {dt.strftime('%Y-%m-%d')}"])
    ws.append([])
    ws.append(cols)
    for c in range(1, len(cols) + 1):
        cell = ws.cell(row=4, column=c)
        cell.fill = head_fill
        cell.font = head_font
        cell.alignment = Alignment(horizontal="center")
    for r in rows:
        ws.append(safe_row(r))
    for i, col in enumerate(cols, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = max(14, len(str(col)) + 4)
    buf = io.BytesIO()
    await asyncio.to_thread(wb.save, buf)
    buf.seek(0)
    fname = f"{report_key}_{df.strftime('%Y%m%d')}_{dt.strftime('%Y%m%d')}.xlsx"
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@router.get("/export-csv/{report_key}")
async def export_report_csv(
    report_key: str,
    date_from: str = Query(None),
    date_to: str = Query(None),
    location_id: str = Query(None),
    role_id: str = Query(None),
    employee_id: str = Query(None),
    filters: ReportFilters = Depends(),
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Stream any registered report as CSV (BI-friendly, opens anywhere)."""
    await _assert_report_access(db, current, report_key, export=True)
    if report_key not in REPORTS:
        from fastapi import HTTPException
        raise HTTPException(404, "Unknown report")
    import csv
    _tab, _title, builder = REPORTS[report_key]
    location_id = await _scoped_location(db, current, location_id)
    tz = await shop_tzname(db, current.org_id, location_id)
    df, dt = _report_window(date_from, date_to, tz)
    df = await _report_from(db, current, report_key, df)
    cols, rows = await builder(db, current.org_id, df, dt, tz=tz,
                               **await _builder_kw(db, current, report_key, location_id, role_id, employee_id, filters))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        w.writerow(safe_row(r))
    buf.seek(0)
    fname = f"{report_key}_{df.strftime('%Y%m%d')}_{dt.strftime('%Y%m%d')}.csv"
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@router.get("/company-dashboard")
async def company_dashboard(
    location_id: Optional[str] = None,        # build 137: scope the dashboard to one branch
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Rich 'My company' dashboard matching Orderry: today metrics, status mix,
    orders-by-assignee, account balances, and monthly KPIs. Optionally scoped to a
    single location via ?location_id=.

    PERF-07: the figures are the same for everyone who opens Home in this shop (and branch), so
    they are kept for HOME_DASHBOARD_CACHE_S seconds (Redis when set, so every worker shares
    them); what money a caller may see is decided per request, after the cache."""
    from backend.config import settings
    oid = current.org_id
    location_id = await _scoped_location(db, current, location_id)
    ttl = int(getattr(settings, "HOME_DASHBOARD_CACHE_S", 0) or 0)
    ckey, rkey = ("home", oid, location_id or ""), f"home:{oid}:{location_id or ''}"
    out = await _dash_get(oid, key=ckey, redis_key=rkey) if ttl > 0 else None
    if out is None:
        out = await _company_figures(db, oid, location_id)
        if ttl > 0:
            await _dash_put(oid, out, key=ckey, redis_key=rkey, ttl=ttl)

    # Money on Home follows the role: balances need "see balance", takings and amounts owed
    # need finance access. Counts stay visible to everyone.
    from backend.services.auth import has_perm
    see_bal = await has_perm(db, current, "can_view_balance")
    see_fin = await has_perm(db, current, "can_view_finance")
    hide = {"today": ("revenue", "paid", "refunds", "cash_collected", "net_sales") if not see_fin else (),
            "kpis": (("receivables", "outstanding", "ar", "ap") if not see_fin else ())
                    + (("total_money", "cash", "cashless") if not see_bal else ()),
            "monthly": ("revenue", "paid", "cash_collected", "net_sales", "avg_work_order") if not see_fin else ()}
    return {k: ({f: (None if f in hide.get(k, ()) else v) for f, v in val.items()} if isinstance(val, dict) else val)
            for k, val in out.items()}


async def _company_figures(db, oid: str, location_id: Optional[str]) -> dict:
    """Home's figures for the whole shop (or one branch), before any role hides money."""
    from backend.models import Account, OrderStatus
    now = now_utc()
    tz = await shop_tzname(db, oid)   # today/this month start on the shop's clock, not UTC's
    today = today_in(tz)
    today0 = day_start_utc(today, tz)
    month0 = day_start_utc(today.replace(day=1), tz)

    # Everything below is aggregated in the database, per status (a shop has a handful): Home
    # used to load every work order the shop ever had, and ten opens on a 30k-order shop took
    # both workers to 4.8 GB. The status groups are applied to those few rows in Python.
    from sqlalchemy import case
    scope = [Order.org_id == oid, Order.is_estimate == False]  # noqa: E712
    if location_id:
        scope.append(Order.location_id == location_id)
    statuses = (await db.execute(select(OrderStatus).where(OrderStatus.org_id == oid, OrderStatus.record_type == "work_order"))).scalars().all()
    grp = {s.key: s.status_group for s in statuses}
    closed_groups = {"closed", "dropped"}
    # the shop's dropped keys (revenue.dropped_keys), read once here for every figure below
    dropped = {k for k, g in grp.items() if g == "dropped"}

    def is_open(status): return grp.get(status) not in closed_groups

    def n_if(cond): return func.coalesce(func.sum(case((cond, 1), else_=0)), 0)

    per_status = (await db.execute(select(
        Order.status, func.count(),
        n_if(Order.created_at >= today0),
        n_if(Order.priority >= 1),
        n_if(Order.due_at < now),
        n_if(Order.created_at >= month0),
    ).where(*scope).group_by(Order.status))).all()
    open_rows = [r for r in per_status if is_open(r[0])]
    closed_rows = [r for r in per_status if grp.get(r[0]) in closed_groups]

    wo_today = sum(r[2] for r in per_status)
    # Two different measures, both labelled (services/revenue): "revenue" is net sales (ex tax,
    # after returns — the same figure as the dashboard, finance summary and P&L); "paid" is the
    # money that came in ("Paid today"), with refunds and cash_collected beside it.
    cash_today, cash_month = await money_rules.cash_collected_windows(
        db, oid, [(today0, None), (month0, None)], location_id)
    sales_today = (await money_rules.net_sales(db, oid, today0, None, location_id, dropped=dropped))["revenue"]

    new_count = sum(r[1] for r in per_status if grp.get(r[0]) == "new")
    urgent = sum(r[3] for r in open_rows)
    overdue = sum(r[4] for r in open_rows)
    receivables = await money_rules.receivables(db, oid, location_id, dropped=dropped)

    # accounts (cash vs cashless), and Build 124's true A/R and A/P from the unified ledger's
    # running balances (ar = what clients owe us, the positive contact balances; ap = what we owe
    # suppliers), summed in the database in one statement
    from backend.models import Client, Supplier
    acct = [Account.org_id == oid, Account.active == True]  # noqa: E712
    cash_bal, cashless_bal, ar, ap = (float(x or 0) for x in (await db.execute(select(
        select(func.coalesce(func.sum(Account.balance), 0)).where(
            *acct, Account.payment_option == "cash").scalar_subquery(),
        select(func.coalesce(func.sum(Account.balance), 0)).where(
            *acct, func.coalesce(Account.payment_option, "") != "cash").scalar_subquery(),
        select(func.coalesce(func.sum(Client.balance), 0)).where(
            Client.org_id == oid, Client.balance > 0).scalar_subquery(),
        select(func.coalesce(func.sum(Supplier.balance), 0)).where(
            Supplier.org_id == oid, Supplier.balance > 0).scalar_subquery()))).one())

    # status mix (open orders only, by group)
    mix = {}
    for r in open_rows:
        g = grp.get(r[0], "new")
        mix[g] = mix.get(g, 0) + r[1]

    # orders by assignee (open)
    by_assignee = {}
    for name, status, n in (await db.execute(
            select(Employee.name, Order.status, func.count())
            .select_from(Order).outerjoin(Employee, Employee.id == Order.assigned_to_id)
            .where(*scope).group_by(Employee.name, Order.status))).all():
        if not is_open(status):
            continue
        b = by_assignee.setdefault(name or "Unassigned", {"new": 0, "in_progress": 0})
        b["new" if grp.get(status) == "new" else "in_progress"] += n
    assignee_rows = sorted(
        [{"name": k, "new": v["new"], "in_progress": v["in_progress"], "total": v["new"] + v["in_progress"]}
         for k, v in by_assignee.items()], key=lambda r: -r["total"])[:8]

    # monthly KPIs
    wo_month = sum(r[5] for r in per_status)
    won_month = sum(r[5] for r in closed_rows)
    month_jobs: dict = {}
    sales_month = (await money_rules.net_sales(db, oid, month0, None, location_id, dropped=dropped,
                                               jobs=month_jobs))["revenue"]
    # RPT-08: the average job finished THIS MONTH (it averaged every closed job ever): the jobs
    # earned this month, ex tax after refunds and credit notes, dropped jobs left out — the same
    # jobs and figures as this month's revenue above (services/revenue)
    finished = [f["revenue"] for f in month_jobs.values() if not f["dropped"]]
    avg_wo = q2(sum(finished) / len(finished)) if finished else 0
    conversion = round(won_month / wo_month * 100, 0) if wo_month else 0

    return {
        "today": {
            "work_orders": wo_today,
            "revenue": sales_today,                     # net sales, ex tax
            "paid": cash_today["in"],                   # money in ("Paid today")
            "refunds": cash_today["refunds"],
            "cash_collected": cash_today["net"],        # in − refunds
            "net_sales": sales_today,                   # revenue, ex tax
        },
        "kpis": {
            "new": new_count, "urgent": urgent, "overdue": overdue,
            "receivables": receivables,
            "outstanding": receivables,                 # same figure as Reports/Finance
            # client balances from the contact ledger — a different measure from receivables
            "ar": q2(float(ar)), "ap": q2(float(ap)),
            "total_money": q2(cash_bal + cashless_bal),
            "cash": q2(cash_bal), "cashless": q2(cashless_bal),
        },
        "status_mix": [{"group": g, "count": c} for g, c in mix.items()],
        "by_assignee": assignee_rows,
        "monthly": {
            "work_orders_created": wo_month,
            "work_orders_won": won_month,
            "revenue": sales_month,                     # net sales, ex tax
            "paid": cash_month["in"],                   # money in ("Paid")
            "cash_collected": cash_month["net"],
            "net_sales": sales_month,
            "conversion": conversion,
            "avg_work_order": avg_wo,
        },
    }


# ══════════════════════════════════════════════════════════════════════════
#  Build 144bf — Job Profitability report (Reports only; read-only).
#  Margin per closed work order = revenue − parts cost (FIFO/avg, already
#  stamped on the line) − labour cost (Σ TimeEntry duration × snapshot rate,
#  timer AND manual). Rolled up per location. No schema, no WO-page changes.
# ══════════════════════════════════════════════════════════════════════════


async def _scoped_location(db, current, location_id):
    """The branch a report or dashboard may cover for this caller. A role limited to some
    locations can only ask for one of them, and with no choice gets its only branch (or is
    told to pick one) instead of the whole shop's figures."""
    from fastapi import HTTPException

    from backend.models import Role
    from backend.services.auth import check_location_access, is_shop_owner
    if not current.role_id or await is_shop_owner(db, current):
        return location_id
    if location_id:
        await check_location_access(db, current, location_id)
        return location_id
    role = (await db.execute(select(Role).where(Role.id == current.role_id))).scalar_one_or_none()
    allow = _allowed_location_ids(role)
    if allow is None:
        return None
    if len(allow) == 1:
        return next(iter(allow))
    raise HTTPException(403, "Choose one of your locations to see this report")


def _allowed_location_ids(role):
    """Return the location-id allowlist for a scoped role, or None = all."""
    if not role or getattr(role, "location_scope", "all") != "selected":
        return None
    import json as _j
    try:
        return set(_j.loads(role.location_ids or "[]"))
    except Exception:
        return set()


@router.get("/job-profitability")
async def job_profitability(
    from_: Optional[str] = None,
    to: Optional[str] = None,
    location_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_cashflow")),
):
    """Profit per successful job closed in the period (dropped jobs are left out — see Total
    profit). The figures come from services/profit.job_figures, the same as every profit screen."""
    from backend.models import Location, Role
    from backend.services.profit import job_figures
    oid = current.org_id

    # date window (default: trailing 30 days), on the day each job was earned
    # Same window rules as every report: a date-only "to" covers that whole day.
    _tz = await shop_tzname(db, oid, location_id)
    end = _parse_range(None, to, _tz)[1] if to else now_utc()
    start = _parse_range(from_, None, _tz)[0] if from_ else (end - timedelta(days=30))

    # honour a location-scoped role
    role = None
    if current.role_id:
        role = (await db.execute(select(Role).where(Role.id == current.role_id))).scalar_one_or_none()
    allow = _allowed_location_ids(role)

    # jobs earned in the window — closed, or first billed on an invoice staff sent: the date rule
    # net sales uses (services/revenue). Dropped jobs are left out here (see Total profit).
    earned = await money_rules.earned_jobs_query(db, oid, start, end, location_id, allowed_locations=allow)
    orders = (await db.execute(earned)).scalars().all()
    if not orders:
        return {"rows": [], "by_location": [], "totals": {
            "revenue": 0, "tax": 0, "parts_cost": 0, "labour_cost": 0, "commission": 0, "margin": 0,
            "margin_pct": 0, "jobs": 0}, "range": {"from": start.isoformat(), "to": end.isoformat()}}
    figs = await job_figures(db, oid, earned.with_only_columns(Order.id))   # sums in SQL, no id list

    # client + location names
    cids = {o.client_id for o in orders if o.client_id}
    lids = {o.location_id for o in orders if o.location_id}
    cname = {c.id: c.name for c in (await db.execute(
        select(Client).where(Client.id.in_(cids), Client.org_id == oid))).scalars().all()} if cids else {}
    lname = {loc.id: loc.name for loc in (await db.execute(
        select(Location).where(Location.id.in_(lids), Location.org_id == oid))).scalars().all()} if lids else {}

    keys = ("revenue", "tax", "parts_cost", "labour_cost", "commission", "margin")
    rows = []
    loc_agg: dict = {}
    tot = {k: 0.0 for k in keys}
    for o in orders:
        f = figs[o.id]
        vals = {"revenue": f["revenue"], "tax": f["tax"], "parts_cost": f["cogs"], "labour_cost": f["labour_cost"],
                "commission": f["commission"], "margin": f["profit"]}
        rows.append({
            "order_id": o.id, "order_number": o.order_number,
            "client_name": cname.get(o.client_id), "location_id": o.location_id,
            "location_name": lname.get(o.location_id), **vals,
            "margin_pct": round(f["profit"] / f["revenue"] * 100, 1) if f["revenue"] else 0.0,
            "clocked_minutes": f["clocked_minutes"], "has_labour": f["clocked_minutes"] > 0,
        })
        la = loc_agg.setdefault(o.location_id, {
            "location_id": o.location_id, "name": lname.get(o.location_id) or "—", "jobs": 0,
            **{k: 0.0 for k in keys}})
        la["jobs"] += 1
        for k in keys:
            tot[k] = q2(tot[k] + vals[k])
            la[k] = q2(la[k] + vals[k])

    rows.sort(key=lambda r: r["margin"], reverse=True)
    by_location = [{**la, "margin_pct": round(la["margin"] / la["revenue"] * 100, 1) if la["revenue"] else 0.0}
                   for la in loc_agg.values()]
    by_location.sort(key=lambda r: r["margin"], reverse=True)
    from backend.services.redact import can_see_costs
    if not await can_see_costs(db, current):
        rows.sort(key=lambda r: r["revenue"], reverse=True)
        return _hide_profit_figures({"rows": rows, "by_location": by_location, "totals": {**tot, "jobs": len(rows)},
                                     "range": {"from": start.isoformat(), "to": end.isoformat()}})
    return {
        "rows": rows,
        "by_location": by_location,
        "totals": {**tot, "margin_pct": round(tot["margin"] / tot["revenue"] * 100, 1) if tot["revenue"] else 0.0,
                   "jobs": len(rows)},
        "range": {"from": start.isoformat(), "to": end.isoformat()},
    }


@router.get("/total-profit")
async def total_profit_report(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    location_id: Optional[str] = Query(None),
    technician_id: Optional[str] = Query(None, max_length=64),
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_see_profit")),
):
    """Total profit: successful jobs + dropped jobs + counter sales for the period, after tax and
    commissions, with the previous period of the same length, by location and technician, and
    the rows behind every number. Cost and profit fields are removed for roles that can't see
    purchase prices (core/cost_redaction)."""
    return await _total_profit_for(db, current, date_from, date_to, location_id, technician_id)


@router.get("/total-profit.csv")
async def total_profit_csv(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    location_id: Optional[str] = Query(None),
    technician_id: Optional[str] = Query(None, max_length=64),
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_see_profit")),
):
    """Total profit's rows as CSV, for the accountant (the same rows and figures as the page)."""
    import csv

    from backend.services.auth import assert_perm
    await assert_perm(db, current, "can_export_sales")
    out = await _total_profit_for(db, current, date_from, date_to, location_id, technician_id)
    kinds = {"job": "Job", "dropped": "Dropped job", "sale": "Counter sale", "other": "Refunds and other"}
    fields = [("Sales", "revenue"), ("Tax", "tax"), ("Parts cost", "cogs"),
              ("Labour cost", "labour_cost"), ("Commission", "commission"), ("Profit", "profit"),
              ("Written off", "written_off")]
    fields = [(label, key) for label, key in fields if any(key in r for r in out["rows"]) or not out["rows"]]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Type", "Number", "Date", "Location", "Technician", "Customer", "Reason", *[f[0] for f in fields]])
    for r in out["rows"]:
        w.writerow(safe_row([kinds.get(r["kind"], r["kind"]), r.get("number") or "", (r.get("date") or "")[:10],
                             r.get("location_name") or "", r.get("employee_name") or "", r.get("client_name") or "",
                             r.get("reason") or "", *[r.get(key, "") for _, key in fields]]))
    t = out["totals"]
    w.writerow(safe_row(["Total", "", "", "", "", "", "", *[t.get(key, "") for _, key in fields]]))
    rng = out["range"]
    fname = f"total_profit_{rng['from'][:10].replace('-', '')}_{rng['to'][:10].replace('-', '')}.csv"
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{fname}"'})


async def _total_profit_for(db, current, date_from, date_to, location_id, technician_id) -> dict:
    from fastapi import HTTPException

    from backend.models import Role
    from backend.services.auth import assert_perm
    from backend.services.profit import total_profit
    from backend.services.redact import can_see_costs
    await assert_perm(db, current, "can_view_reports")
    location_id = await _scoped_location(db, current, location_id)
    try:
        df, dt = _parse_range(date_from, date_to, await shop_tzname(db, current.org_id, location_id))
    except ValueError:
        raise HTTPException(422, "Use dates like 2026-10-24")
    if dt - df > timedelta(days=731):
        raise HTTPException(422, "Pick a period of two years or less")
    role = (await db.execute(select(Role).where(Role.id == current.role_id))).scalar_one_or_none() \
        if current.role_id else None
    out = await total_profit(db, current.org_id, df, dt, location_id=location_id,
                             technician_id=technician_id, allowed_locations=_allowed_location_ids(role))
    if not await can_see_costs(db, current):
        _hide_profit_figures(out)
    return out


# Cost-like figures the shared redaction list doesn't name (they're fine elsewhere, e.g. payroll).
_PROFIT_ONLY_KEYS = ("labour_cost", "commission", "parts_cost", "profit_pct", "margin")


def _hide_profit_figures(obj):
    """For a viewer who can't see purchase prices: drop every cost/profit figure from a profit
    report (the middleware drops cogs/profit/margin_pct) and stop sorting by profit, since the
    order alone would give the ranking away."""
    if isinstance(obj, dict):
        for k in _PROFIT_ONLY_KEYS:
            obj.pop(k, None)
        for k, v in obj.items():
            if isinstance(v, list) and v and isinstance(v[0], dict) and "revenue" in v[0] and k != "rows":
                v.sort(key=lambda r: r.get("revenue") or 0, reverse=True)
            _hide_profit_figures(v)
    elif isinstance(obj, list):
        for v in obj:
            _hide_profit_figures(v)
    return obj


@router.get("/home-feed")
async def home_feed(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """The Home 'needs you' feed — real, org-scoped signals assembled from existing data.
    Each item is a group with a count and a deep link to the filtered list behind it.
    Deterministic (no AI); the order is roughly by urgency. Returns ok=True when the
    shop is all caught up."""
    from backend.models import Lead, Role
    oid = current.org_id
    now = now_utc()

    # Role shapes the feed: order_scope="own" limits order/task signals to what's
    # assigned to this person; home_feed_sources (owner-set, in the roles section)
    # limits which signals show at all. Null/empty source list = show all.
    role = (await db.execute(select(Role).where(Role.id == current.role_id))).scalar_one_or_none() if current.role_id else None
    own = bool(role and role.order_scope == "own")
    sources = set(role.home_feed_sources) if role and role.home_feed_sources else None

    statuses = (await db.execute(select(OrderStatus).where(OrderStatus.org_id == oid))).scalars().all()
    closed_keys = {s.key for s in statuses if (s.status_group or "") in ("closed", "dropped")}
    ready_keys = {s.key for s in statuses if (s.status_group or "") == "ready"}

    open_conds = [Order.org_id == oid, Order.closed_at == None]  # noqa: E711
    if closed_keys:
        open_conds.append(Order.status.notin_(closed_keys))
    if own:
        open_conds.append(Order.assigned_to_id == current.id)

    async def count(*conds) -> int:
        return (await db.execute(select(func.count()).select_from(Order).where(*conds))).scalar() or 0

    mine = [Order.assigned_to_id == current.id] if own else []
    overdue = await count(*open_conds, Order.due_at != None, Order.due_at < now)  # noqa: E711
    unread = await count(Order.org_id == oid, Order.closed_at == None, Order.client_msg_unread > 0, *mine)  # noqa: E711
    urgent = await count(*open_conds, Order.priority >= 1)
    ready = await count(Order.org_id == oid, Order.closed_at == None, Order.status.in_(ready_keys), *mine) if ready_keys else 0  # noqa: E711

    # a dropped job's open invoice isn't owed any more (its balance was written off)
    dropped_jobs = select(Order.id).where(Order.org_id == oid, Order.status.in_(await money_rules.dropped_keys(db, oid)))
    unpaid_n = (await db.execute(select(func.count()).select_from(Invoice).where(
        Invoice.org_id == oid, Invoice.status.in_(["draft", "partial", "issued"]),
        (Invoice.total - Invoice.amount_paid - Invoice.credited) > 0,
        Invoice.order_id.is_(None) | ~Invoice.order_id.in_(dropped_jobs)))).scalar() or 0

    # the same rule as the dashboard tile and the low-stock report (services/revenue)
    low = (await db.execute(low_stock_count_query(oid))).scalar() or 0

    task_conds = [Task.org_id == oid, Task.status.notin_(("done", "cancelled")), Task.archived_at.is_(None)]
    if own:
        task_conds.append(assigned_sql(current.id))
    else:   # tk02: staff who don't assign work count only the tasks they can see
        task_conds += await visibility_conds(db, current)
    open_tasks = (await db.execute(select(func.count()).select_from(Task).where(*task_conds))).scalar() or 0
    inquiries = (await db.execute(select(func.count()).select_from(Lead).where(
        Lead.org_id == oid, Lead.status.in_(["new", "in_progress"]))) ).scalar() or 0

    items = []

    def add(key, kind, icon, n, title, to):
        if sources is not None and key not in sources:
            return  # owner turned this signal off for this role
        if n and n > 0:
            items.append({"key": key, "kind": kind, "icon": icon, "count": int(n), "title": title, "to": to})

    add("overdue", "danger", "warning", overdue, f"{overdue} work order{'s' if overdue != 1 else ''} overdue", "/orders")
    add("messages", "danger", "chat", unread, f"{unread} customer message{'s' if unread != 1 else ''} waiting", "/orders")
    add("ready", "success", "local_shipping", ready, f"{ready} order{'s' if ready != 1 else ''} ready for pickup", "/orders")
    add("urgent", "warning", "priority_high", urgent, f"{urgent} urgent order{'s' if urgent != 1 else ''} open", "/orders?priority=urgent")
    add("unpaid", "info", "request_quote", unpaid_n, f"{unpaid_n} unpaid invoice{'s' if unpaid_n != 1 else ''}", "/invoices")
    add("lowstock", "warning", "inventory_2", low, f"{low} item{'s' if low != 1 else ''} low or out of stock", "/inventory")
    add("tasks", "info", "task_alt", open_tasks, f"{open_tasks} open task{'s' if open_tasks != 1 else ''}", "/tasks")
    add("inquiries", "info", "mail", inquiries, f"{inquiries} inquir{'ies' if inquiries != 1 else 'y'} awaiting response", "/inquiries")

    return {"items": items, "ok": len(items) == 0}
