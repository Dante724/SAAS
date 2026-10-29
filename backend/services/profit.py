"""Job profit — the one place it is worked out.

Job profitability, Total profit, Work orders profit and the dropped-jobs report all call
`job_figures`, so no two screens can show different profit for the same job.

Per job (money in the org's base currency):
- charged      what the shop earns from the job: its total less credit notes. A dropped job earns
               only the money it actually kept (net paid, capped at that) — the part written off
               when it was dropped is never revenue.
- tax          the lines' tax, scaled to what was charged; tax collected isn't the shop's money.
- revenue      charged − tax.
- cogs         Σ qty × cost of the job's product / part lines (FIFO/average cost stamped on the line).
- labour_cost  Σ clocked hours × the cost rate snapshotted on each time entry (timer and manual).
- commission   net of the payroll ledger for the job and its lines: accruals, claw-backs, reversals.
- profit       revenue − cogs − labour_cost − commission.

Counter sales (`sale_figures`): revenue = sale total, cogs = the FIFO cost stamped on the sale;
voided sales don't count. Sales carry no tax or commission in Tekly today, so those are 0.
"""
from datetime import datetime, timedelta
from typing import Iterable, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    Client, Employee, EmployeeBalanceLedger, Invoice, InvoiceOrder, Location, Order, OrderLine,
    OrderStatus, Payment, Sale, TimeEntry, WorkOrderType,
)
from backend.services.money import q2

FIELDS = ("revenue", "tax", "cogs", "labour_cost", "commission", "profit", "written_off")


async def dropped_keys(db: AsyncSession, org_id: str) -> set[str]:
    return set((await db.execute(select(OrderStatus.key).where(
        OrderStatus.org_id == org_id, OrderStatus.status_group == "dropped",
        OrderStatus.record_type == "work_order"))).scalars().all())


def _line_tax(line) -> float:
    from backend.routers.orders import _line_tax_amount
    try:
        return _line_tax_amount(line)
    except Exception:   # noqa: BLE001 — a malformed legacy line counts as untaxed
        return 0.0


async def job_figures(db: AsyncSession, org_id: str, orders: list[Order],
                      dropped: Optional[set[str]] = None) -> dict[str, dict]:
    """{order_id: figures} for these jobs (all of the same org)."""
    if not orders:
        return {}
    if dropped is None:
        dropped = await dropped_keys(db, org_id)
    ids = [o.id for o in orders]

    lines = (await db.execute(select(OrderLine).where(OrderLine.order_id.in_(ids)))).scalars().all()
    tax: dict = {}
    cogs: dict = {}
    line_ids: dict = {}
    for ln in lines:
        tax[ln.order_id] = tax.get(ln.order_id, 0.0) + _line_tax(ln)
        if ln.line_type == "part" or ln.kind == "product":
            cogs[ln.order_id] = cogs.get(ln.order_id, 0.0) + float(ln.cost_price or 0) * float(ln.qty or 0)
        line_ids[ln.id] = ln.order_id

    labour: dict = {}
    minutes: dict = {}
    for e in (await db.execute(select(TimeEntry).where(
            TimeEntry.order_id.in_(ids), TimeEntry.ended_at.isnot(None),
            TimeEntry.state.in_(("stopped", "approved"))))).scalars().all():
        hrs = (e.duration_sec or 0) / 3600.0
        labour[e.order_id] = labour.get(e.order_id, 0.0) + hrs * float(e.cost_rate or 0)
        minutes[e.order_id] = minutes.get(e.order_id, 0.0) + (e.duration_sec or 0) / 60.0

    commission: dict = {}
    refs = ids + list(line_ids)
    L = EmployeeBalanceLedger
    for ref, amt in (await db.execute(select(L.ref_id, func.coalesce(func.sum(L.amount), 0)).where(
            L.org_id == org_id, L.entry_type.in_(("commission", "clawback")), L.ref_id.in_(refs))
            .group_by(L.ref_id))).all():
        oid = ref if ref in set(ids) else line_ids.get(ref)
        if oid:
            commission[oid] = commission.get(oid, 0.0) + float(amt or 0)

    # Money kept on dropped jobs (payments − refunds), one grouped query.
    drop_ids = [o.id for o in orders if o.status in dropped]
    kept: dict = {}
    if drop_ids:
        for oid, kind, amt in (await db.execute(
                select(Payment.order_id, Payment.kind, func.coalesce(func.sum(Payment.amount), 0))
                .where(Payment.order_id.in_(drop_ids)).group_by(Payment.order_id, Payment.kind))).all():
            sign = -1 if kind == "refund" else (1 if kind in ("payment", "prepayment") else 0)
            kept[oid] = kept.get(oid, 0.0) + sign * abs(float(amt or 0))

    # Credit notes: only for jobs billed on an invoice that has any.
    credited_ids = set((await db.execute(select(Invoice.order_id).where(
        Invoice.order_id.in_(ids), Invoice.credited > 0))).scalars().all())
    credited_ids |= set((await db.execute(select(InvoiceOrder.order_id).join(
        Invoice, Invoice.id == InvoiceOrder.invoice_id).where(
        InvoiceOrder.order_id.in_(ids), Invoice.credited > 0))).scalars().all())
    credit: dict = {}
    if credited_ids:
        from backend.services.order_money import credited
        for oid in credited_ids:
            credit[oid] = await credited(db, oid)

    out = {}
    for o in orders:
        total = float(o.total or 0)
        billable = max(0.0, total - credit.get(o.id, 0.0))
        is_dropped = o.status in dropped
        charged = min(max(kept.get(o.id, 0.0), 0.0), billable) if is_dropped else billable
        t = q2(tax.get(o.id, 0.0) * (charged / total)) if total else 0.0
        rev = q2(charged - t)
        c, lab, com = q2(cogs.get(o.id, 0.0)), q2(labour.get(o.id, 0.0)), q2(commission.get(o.id, 0.0))
        out[o.id] = {
            "dropped": is_dropped, "total": q2(total), "charged": q2(charged), "tax": t, "revenue": rev,
            "cogs": c, "labour_cost": lab, "commission": com, "profit": q2(rev - c - lab - com),
            "written_off": q2(float(getattr(o, "written_off", 0) or 0)) if is_dropped else 0.0,
            "clocked_minutes": round(minutes.get(o.id, 0.0), 1),
        }
    return out


def sale_figures(s: Sale) -> dict:
    rev, c = q2(float(s.total or 0)), q2(float(s.total_cost or 0))
    return {"dropped": False, "total": rev, "charged": rev, "tax": 0.0, "revenue": rev, "cogs": c,
            "labour_cost": 0.0, "commission": 0.0, "profit": q2(rev - c), "written_off": 0.0,
            "clocked_minutes": 0.0}


def closed_jobs_query(org_id: str, df: datetime, dt: datetime, *, location_id: Optional[str] = None,
                      technician_id: Optional[str] = None, allowed_locations: Optional[set] = None):
    """Work orders closed (or dropped) in the period — not estimates, not deleted."""
    q = select(Order).where(Order.org_id == org_id, Order.is_estimate == False,  # noqa: E712
                            Order.archived == False,  # noqa: E712
                            Order.closed_at.isnot(None), Order.closed_at >= df, Order.closed_at <= dt)
    if location_id:
        q = q.where(Order.location_id == location_id)
    if allowed_locations is not None:
        q = q.where(Order.location_id.in_(allowed_locations or {""}))
    if technician_id:
        q = q.where(Order.assigned_to_id == technician_id)
    return q


def _sales_query(org_id, df, dt, *, location_id=None, technician_id=None, allowed_locations=None):
    q = select(Sale).where(Sale.org_id == org_id, Sale.voided_at.is_(None),
                           Sale.created_at >= df, Sale.created_at <= dt)
    if location_id:
        q = q.where(Sale.location_id == location_id)
    if allowed_locations is not None:
        q = q.where(Sale.location_id.in_(allowed_locations or {""}))
    if technician_id:
        q = q.where(Sale.seller_id == technician_id)
    return q


def _zero() -> dict:
    return {"count": 0, **{k: 0.0 for k in FIELDS}}


def _add(acc: dict, f: dict) -> None:
    acc["count"] += 1
    for k in FIELDS:
        acc[k] = q2(acc[k] + f[k])


async def _period(db, org_id, df, dt, **flt) -> tuple[dict, dict, list]:
    """(buckets, totals, rows) for one period."""
    dropped = await dropped_keys(db, org_id)
    orders = (await db.execute(closed_jobs_query(org_id, df, dt, **flt))).scalars().all()
    figs = await job_figures(db, org_id, list(orders), dropped)
    sales = (await db.execute(_sales_query(org_id, df, dt, **flt))).scalars().all()
    buckets = {"jobs": _zero(), "dropped": _zero(), "sales": _zero()}
    rows = []
    for o in orders:
        f = figs[o.id]
        kind = "dropped" if f["dropped"] else "job"
        _add(buckets["dropped" if f["dropped"] else "jobs"], f)
        rows.append({"kind": kind, "id": o.id, "number": o.order_number, "date": o.closed_at.isoformat(),
                     "location_id": o.location_id, "employee_id": o.assigned_to_id, "client_id": o.client_id,
                     "type_id": o.type_id, "status": o.status,
                     "reason": o.cancel_reason if f["dropped"] else None, **_pick(f)})
    for s in sales:
        f = sale_figures(s)
        _add(buckets["sales"], f)
        rows.append({"kind": "sale", "id": s.id, "number": s.sale_number, "date": s.created_at.isoformat(),
                     "location_id": s.location_id, "employee_id": s.seller_id, "client_id": s.client_id,
                     "type_id": None, "status": None, "reason": None, **_pick(f)})
    totals = _zero()
    for b in buckets.values():
        totals["count"] += b["count"]
        for k in FIELDS:
            totals[k] = q2(totals[k] + b[k])
    return buckets, totals, rows


def _pick(f: dict) -> dict:
    return {k: f[k] for k in FIELDS}


def _pct(cur: float, prev: float) -> Optional[float]:
    return round((cur - prev) / abs(prev) * 100, 1) if prev else None


async def total_profit(db: AsyncSession, org_id: str, df: datetime, dt: datetime, *,
                       location_id: Optional[str] = None, technician_id: Optional[str] = None,
                       allowed_locations: Optional[Iterable[str]] = None) -> dict:
    """Jobs + dropped jobs + counter sales for the period, after tax and commissions; the same
    for the previous period of equal length; by location and technician; and the rows behind it."""
    allow = set(allowed_locations) if allowed_locations is not None else None
    flt = {"location_id": location_id, "technician_id": technician_id, "allowed_locations": allow}
    buckets, totals, rows = await _period(db, org_id, df, dt, **flt)
    p_dt = df - timedelta(seconds=1)          # the period of equal length just before
    p_df = p_dt - (dt - df)
    p_buckets, p_totals, _ = await _period(db, org_id, p_df, p_dt, **flt)

    names = await _names(db, org_id, rows)
    for r in rows:
        r["location_name"] = names["loc"].get(r["location_id"])
        r["employee_name"] = names["emp"].get(r["employee_id"])
        r["client_name"] = names["cli"].get(r["client_id"])
        r["type_name"] = names["type"].get(r["type_id"])
    rows.sort(key=lambda r: r["date"], reverse=True)

    def group(key: str, name_key: str) -> list:
        g: dict = {}
        for r in rows:
            k = r[key]
            a = g.setdefault(k, {key: k, "name": r[name_key] or "—", "jobs": 0, "dropped": 0, "sales": 0,
                                 **{f: 0.0 for f in FIELDS}})
            a["jobs" if r["kind"] == "job" else ("dropped" if r["kind"] == "dropped" else "sales")] += 1
            for f in FIELDS:
                a[f] = q2(a[f] + r[f])
        return sorted(g.values(), key=lambda a: a["profit"], reverse=True)

    change = {k: q2(totals[k] - p_totals[k]) for k in ("revenue", "profit")}
    change.update({"profit_pct": _pct(totals["profit"], p_totals["profit"]),
                   "revenue_pct": _pct(totals["revenue"], p_totals["revenue"])})
    margin = round(totals["profit"] / totals["revenue"] * 100, 1) if totals["revenue"] else 0.0
    return {
        "range": {"from": df.isoformat(), "to": dt.isoformat()},
        "totals": {**totals, "margin_pct": margin},
        "buckets": buckets,
        "previous": {**p_totals, "buckets": p_buckets, "range": {"from": p_df.isoformat(), "to": p_dt.isoformat()}},
        "change": change,
        "by_location": group("location_id", "location_name"),
        "by_technician": group("employee_id", "employee_name"),
        "rows": rows,
    }


async def _names(db, org_id, rows) -> dict:
    def ids(k):
        return {r[k] for r in rows if r[k]}

    async def m(model, col_ids, label="name"):
        if not col_ids:
            return {}
        return dict((await db.execute(select(model.id, getattr(model, label)).where(
            model.id.in_(col_ids), model.org_id == org_id))).all())
    return {"loc": await m(Location, ids("location_id")), "emp": await m(Employee, ids("employee_id")),
            "cli": await m(Client, ids("client_id")), "type": await m(WorkOrderType, ids("type_id"))}

