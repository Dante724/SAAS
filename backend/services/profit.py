"""Job profit — worked out here, on top of the shop's one revenue definition (services/revenue).

Job profitability, Total profit, Work orders profit and the dropped-jobs report all call
`job_figures`, so no two screens can show different profit for the same job, and every revenue,
tax and cost figure is the one services/revenue gives every other report.

Per job (money in the org's base currency):
- charged      what the shop earns from the job, with tax: its bill less credit notes and refunds
               (services/revenue.job_figures). A dropped job earns only the money it actually kept
               (net paid, capped at the bill) — the part written off when it was dropped is never
               revenue.
- tax          the tax inside what was charged; tax collected isn't the shop's money.
- revenue      charged − tax.
- cogs         Σ qty × cost of the job's product / part lines (FIFO/average cost stamped on the
               line), less parts that came back to the shelf.
- labour_cost  Σ clocked hours × the cost rate snapshotted on each time entry (timer and manual).
- commission   net of the payroll ledger for the job and its lines: accruals, claw-backs, reversals.
- profit       revenue − cogs − labour_cost − commission.

Counter sales (`sale_figures`): revenue = sale total less any tax the till stored, cogs = the FIFO
cost stamped on the sale; voided sales don't count.

Total profit adds one reconciling row so its revenue is the period's net sales exactly: refunds,
returns and credit notes dated in the period that aren't inside a job above (a refund this month
for a job finished last month), and invoices that bill no job.
"""
from datetime import datetime, timedelta
from typing import Iterable, Optional

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    Client, Employee, EmployeeBalanceLedger, Location, Order, OrderLine, Sale, TimeEntry,
    WorkOrderType,
)
from backend.services import revenue as money_rules
from backend.services.money import q2

FIELDS = ("revenue", "tax", "cogs", "labour_cost", "commission", "profit", "written_off")


dropped_keys = money_rules.dropped_keys


async def job_figures(db: AsyncSession, org_id: str, orders, dropped: Optional[set[str]] = None) -> dict[str, dict]:
    """{order_id: figures} for these jobs (all of the same org): a list of orders, or a SELECT of
    order ids (the sums then run in the database, with no per-job parameters)."""
    if not isinstance(orders, Select) and not orders:
        return {}
    if dropped is None:
        dropped = await dropped_keys(db, org_id)
    base = await money_rules.job_figures(db, orders, dropped)
    cond = money_rules._ids_cond

    labour: dict = {}
    minutes: dict = {}
    for oid, cost, secs in (await db.execute(
            select(TimeEntry.order_id,
                   func.sum(func.coalesce(TimeEntry.duration_sec, 0) / 3600.0 * func.coalesce(TimeEntry.cost_rate, 0)),
                   func.sum(func.coalesce(TimeEntry.duration_sec, 0)))
            .where(cond(TimeEntry.order_id, orders), TimeEntry.ended_at.isnot(None),
                   TimeEntry.state.in_(("stopped", "approved")))
            .group_by(TimeEntry.order_id))).all():
        labour[oid], minutes[oid] = float(cost or 0), float(secs or 0) / 60.0

    # commission booked against the job itself, and against its lines
    L = EmployeeBalanceLedger
    kinds = L.entry_type.in_(("commission", "clawback"))
    commission: dict = {}
    for oid, amt in (await db.execute(
            select(L.ref_id, func.coalesce(func.sum(L.amount), 0))
            .where(L.org_id == org_id, kinds, cond(L.ref_id, orders)).group_by(L.ref_id))).all():
        commission[oid] = commission.get(oid, 0.0) + float(amt or 0)
    for oid, amt in (await db.execute(
            select(OrderLine.order_id, func.coalesce(func.sum(L.amount), 0))
            .join(L, L.ref_id == OrderLine.id)
            .where(L.org_id == org_id, kinds, cond(OrderLine.order_id, orders))
            .group_by(OrderLine.order_id))).all():
        commission[oid] = commission.get(oid, 0.0) + float(amt or 0)

    out = {}
    for oid, f in base.items():
        rev, c = q2(f["revenue"]), q2(f["parts_cost"])
        lab, com = q2(labour.get(oid, 0.0)), q2(commission.get(oid, 0.0))
        out[oid] = {
            "dropped": f["dropped"], "total": q2(f["total"]), "charged": q2(f["charged"]), "tax": q2(f["tax"]),
            "revenue": rev, "cogs": c, "labour_cost": lab, "commission": com, "profit": q2(rev - c - lab - com),
            "written_off": q2(f["written_off"]), "clocked_minutes": round(minutes.get(oid, 0.0), 1),
        }
    return out


def sale_figures(s: Sale) -> dict:
    gross, tax = float(s.total or 0), float(getattr(s, "tax", 0) or 0)
    rev, c = q2(gross - tax), q2(float(s.total_cost or 0))
    return {"dropped": False, "total": q2(gross), "charged": q2(gross), "tax": q2(tax), "revenue": rev,
            "cogs": c, "labour_cost": 0.0, "commission": 0.0, "profit": q2(rev - c), "written_off": 0.0,
            "clocked_minutes": 0.0}


def closed_jobs_query(org_id: str, df: datetime, dt: datetime, *, location_id: Optional[str] = None,
                      technician_id: Optional[str] = None, allowed_locations: Optional[set] = None):
    """Work orders closed (or dropped) in the period — not estimates, not deleted. The dropped-jobs
    report lists jobs by the day they were dropped; revenue reports use revenue.earned_jobs_query."""
    q = select(Order).where(Order.org_id == org_id, Order.is_estimate == False,  # noqa: E712
                            Order.archived == False,  # noqa: E712
                            Order.closed_at.isnot(None), Order.closed_at >= df, Order.closed_at <= dt)
    if location_id:
        q = q.where(Order.location_id == location_id)
    if allowed_locations is not None:
        q = q.where(Order.location_id.in_(list(allowed_locations) or [""]))
    if technician_id:
        q = q.where(Order.assigned_to_id == technician_id)
    return q


def _sales_query(org_id, df, dt, *, location_id=None, technician_id=None, allowed_locations=None):
    q = select(Sale).where(Sale.org_id == org_id, Sale.voided_at.is_(None),
                           Sale.created_at >= df, Sale.created_at <= dt)
    if location_id:
        q = q.where(Sale.location_id == location_id)
    if allowed_locations is not None:
        q = q.where(Sale.location_id.in_(list(allowed_locations) or [""]))
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
    """(buckets, totals, rows) for one period: the jobs earned in it (services/revenue's date rule,
    dropped jobs included), counter sales, and the row that reconciles them to net sales."""
    dropped = await dropped_keys(db, org_id)
    eq = await money_rules.earned_jobs_query(db, org_id, df, dt, flt.get("location_id"), include_dropped=True,
                                             technician_id=flt.get("technician_id"),
                                             allowed_locations=flt.get("allowed_locations"), with_date=True)
    jobs = (await db.execute(eq)).all()
    figs = await job_figures(db, org_id, eq.with_only_columns(Order.id), dropped)   # sums in SQL, no id list
    sales = (await db.execute(_sales_query(org_id, df, dt, **flt))).scalars().all()
    buckets = {"jobs": _zero(), "dropped": _zero(), "sales": _zero(), "other": _zero()}
    rows = []
    for o, earned_at in jobs:
        f = figs[o.id]
        kind = "dropped" if f["dropped"] else "job"
        _add(buckets["dropped" if f["dropped"] else "jobs"], f)
        rows.append({"kind": kind, "id": o.id, "number": o.order_number, "date": earned_at.isoformat(),
                     "location_id": o.location_id, "employee_id": o.assigned_to_id, "client_id": o.client_id,
                     "type_id": o.type_id, "status": o.status,
                     "reason": o.cancel_reason if f["dropped"] else None, **_pick(f)})
    for s in sales:
        f = sale_figures(s)
        _add(buckets["sales"], f)
        rows.append({"kind": "sale", "id": s.id, "number": s.sale_number, "date": s.created_at.isoformat(),
                     "location_id": s.location_id, "employee_id": s.seller_id, "client_id": s.client_id,
                     "type_id": None, "status": None, "reason": None, **_pick(f)})
    other = await _reconciling(db, org_id, df, dt, rows, **flt)
    if other:
        _add(buckets["other"], other)
        rows.append({"kind": "other", "id": None, "number": None, "date": dt.isoformat(), "location_id": None,
                     "employee_id": None, "client_id": None, "type_id": None, "status": None,
                     "reason": "Refunds, returns and credit notes in this period not inside a job above, "
                               "and invoices that bill no job", **_pick(other)})
    totals = _zero()
    for b in buckets.values():
        totals["count"] += b["count"]
        for k in FIELDS:
            totals[k] = q2(totals[k] + b[k])
    return buckets, totals, rows


async def _reconciling(db, org_id, df, dt, rows, *, location_id=None, technician_id=None,
                       allowed_locations=None) -> Optional[dict]:
    """What net sales has that the rows don't (see the module docstring), or None when they agree.
    Net sales has no per-technician split, so a technician filter shows the rows alone."""
    if technician_id:
        return None
    if location_id or allowed_locations is None:
        scopes = [location_id]
    else:
        scopes = list(allowed_locations)
    rev = tax = cogs = 0.0
    for loc in scopes:
        ns = await money_rules.net_sales(db, org_id, df, dt, loc)
        rev, tax, cogs = rev + ns["revenue"], tax + ns["tax"], cogs + ns["cogs"]
    rev = q2(rev - sum(r["revenue"] for r in rows))
    tax = q2(tax - sum(r["tax"] for r in rows))
    cogs = q2(cogs - sum(r["cogs"] for r in rows))
    if not (rev or tax or cogs):
        return None
    return {"revenue": rev, "tax": tax, "cogs": cogs, "labour_cost": 0.0, "commission": 0.0,
            "profit": q2(rev - cogs), "written_off": 0.0}


def _pick(f: dict) -> dict:
    return {k: f[k] for k in FIELDS}


def _pct(cur: float, prev: float) -> Optional[float]:
    return round((cur - prev) / abs(prev) * 100, 1) if prev else None


def previous_period(df: datetime, dt: datetime) -> tuple[datetime, datetime]:
    """The period to compare with: whole calendar months compare with the same number of months
    just before (October with September); any other range with the same length just before."""
    whole_months = (df.day == 1 and df.time() == datetime.min.time()
                    and (dt + timedelta(seconds=1)).day == 1 and (dt + timedelta(seconds=1)).time() == datetime.min.time())
    if whole_months:
        months = (dt.year - df.year) * 12 + dt.month - df.month + 1
        y, m = divmod(df.year * 12 + df.month - 1 - months, 12)
        return df.replace(year=y, month=m + 1), df - timedelta(seconds=1)
    p_dt = df - timedelta(seconds=1)
    return p_dt - (dt - df), p_dt


async def total_profit(db: AsyncSession, org_id: str, df: datetime, dt: datetime, *,
                       location_id: Optional[str] = None, technician_id: Optional[str] = None,
                       allowed_locations: Optional[Iterable[str]] = None) -> dict:
    """Jobs + dropped jobs + counter sales for the period, after tax and commissions; the same
    for the previous period of equal length; by location and technician; and the rows behind it."""
    allow = set(allowed_locations) if allowed_locations is not None else None
    flt = {"location_id": location_id, "technician_id": technician_id, "allowed_locations": allow}
    buckets, totals, rows = await _period(db, org_id, df, dt, **flt)
    p_df, p_dt = previous_period(df, dt)
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
            if r["kind"] == "other":
                k, name = "_other", "Not tied to a job or sale"
            else:
                name = r[name_key] or "—"
            a = g.setdefault(k, {key: k if k != "_other" else None, "name": name, "jobs": 0, "dropped": 0,
                                 "sales": 0, **{f: 0.0 for f in FIELDS}})
            if r["kind"] != "other":
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

