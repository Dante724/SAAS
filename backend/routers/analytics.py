"""A9 Owner analytics (Business Insights) + A8 Fleet/B2B accounts.

Read-only aggregations over existing orders/lines/devices — no new tables.
"""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import (
    Client,
    Device,
    Employee,
    Location,
    Order,
    OrderLine,
    OrderStatus,
)
from backend.services.auth import get_current_employee, record_scope
from backend.services.money import q2, q4

router = APIRouter(prefix="/api/analytics", tags=["analytics"],
                   dependencies=[Depends(record_scope)])   # role record scope on {order_id}/{client_id}


def _window(days: int):
    end = datetime.utcnow()
    return end - timedelta(days=days), end


async def _terminal_keys(db, org_id):
    rows = (await db.execute(select(OrderStatus.key).where(
        OrderStatus.org_id == org_id, OrderStatus.is_terminal == True))).scalars().all()  # noqa: E712
    return set(rows)


# ── A9.1 Technician productivity ─────────────────────────────────────────────
@router.get("/technician-productivity")
async def technician_productivity(
    days: int = Query(30, ge=1, le=365),
    location_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Per-technician: jobs closed, avg turnaround (hrs), labor revenue, throughput.
    Attribution is by the order's assignee for closed orders in the window."""
    start, end = _window(days)
    terminal = await _terminal_keys(db, current.org_id)
    q = select(Order).where(Order.org_id == current.org_id, Order.is_estimate == False,  # noqa: E712
                            Order.closed_at.isnot(None), Order.closed_at >= start)
    if location_id:
        q = q.where(Order.location_id == location_id)
    orders = (await db.execute(q)).scalars().all()

    emps = {e.id: e.name for e in (await db.execute(select(Employee).where(
        Employee.org_id == current.org_id))).scalars().all()}
    agg: dict = {}
    for o in orders:
        if o.status not in terminal:
            continue
        tid = o.assigned_to_id or "unassigned"
        a = agg.setdefault(tid, {"jobs": 0, "turnaround_h": 0.0, "revenue": 0.0})
        a["jobs"] += 1
        a["revenue"] += float(o.total or 0)
        if o.created_at and o.closed_at:
            a["turnaround_h"] += (o.closed_at - o.created_at).total_seconds() / 3600.0

    rows = []
    for tid, a in agg.items():
        rows.append({
            "technician_id": None if tid == "unassigned" else tid,
            "technician": "Unassigned" if tid == "unassigned" else emps.get(tid, tid),
            "jobs_closed": a["jobs"],
            "avg_turnaround_hours": round(a["turnaround_h"] / a["jobs"], 1) if a["jobs"] else 0,
            "revenue": q2(a["revenue"]),
            "throughput_per_week": q2(a["jobs"] / (days / 7.0)) if days else 0,
        })
    rows.sort(key=lambda r: r["revenue"], reverse=True)
    return {"period_days": days, "technicians": rows}


# ── A9.2 Multi-location comparison ───────────────────────────────────────────
@router.get("/location-comparison")
async def location_comparison(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    start, _ = _window(days)
    terminal = await _terminal_keys(db, current.org_id)
    locs = {loc.id: loc.name for loc in (await db.execute(select(Location).where(
        Location.org_id == current.org_id))).scalars().all()}
    orders = (await db.execute(select(Order).where(
        Order.org_id == current.org_id, Order.is_estimate == False,  # noqa: E712
        Order.created_at >= start))).scalars().all()

    agg: dict = {}
    for o in orders:
        a = agg.setdefault(o.location_id, {"created": 0, "closed": 0, "revenue": 0.0})
        a["created"] += 1
        if o.status in terminal:
            a["closed"] += 1
            a["revenue"] += float(o.total or 0)
    rows = [{
        "location_id": lid, "location": locs.get(lid, "—"),
        "orders_created": a["created"], "orders_closed": a["closed"],
        "close_rate": round(a["closed"] / a["created"] * 100, 1) if a["created"] else 0,
        "revenue": q2(a["revenue"]),
    } for lid, a in agg.items()]
    rows.sort(key=lambda r: r["revenue"], reverse=True)
    return {"period_days": days, "locations": rows}


# ── A9.3 Trend widget (daily created/closed/revenue) ─────────────────────────
@router.get("/trends")
async def trends(
    days: int = Query(30, ge=7, le=180),
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    start, _ = _window(days)
    terminal = await _terminal_keys(db, current.org_id)
    orders = (await db.execute(select(Order).where(
        Order.org_id == current.org_id, Order.is_estimate == False,  # noqa: E712
        Order.created_at >= start))).scalars().all()
    series: dict = {}
    for o in orders:
        d = o.created_at.date().isoformat() if o.created_at else None
        if not d:
            continue
        s = series.setdefault(d, {"created": 0, "closed": 0, "revenue": 0.0})
        s["created"] += 1
        if o.status in terminal:
            s["closed"] += 1
            s["revenue"] += float(o.total or 0)
    return {"period_days": days,
            "series": [{"date": d, **v} for d, v in sorted(series.items())]}


# ── A8 Fleet / B2B accounts ──────────────────────────────────────────────────
@router.get("/fleet")
async def fleet_overview(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Company (fleet) clients with their asset counts + open-ticket counts."""
    companies = (await db.execute(select(Client).where(
        Client.org_id == current.org_id,
        Client.client_type.in_(("company", "organization"))))).scalars().all()
    terminal = await _terminal_keys(db, current.org_id)
    ids = [cl.id for cl in companies]
    # batch device counts + open-ticket counts (was 2 queries per company)
    dev_counts = dict((await db.execute(select(Device.client_id, func.count()).where(
        Device.client_id.in_(ids)).group_by(Device.client_id))).all()) if ids else {}
    open_counts = dict((await db.execute(select(Order.client_id, func.count()).where(
        Order.org_id == current.org_id, Order.client_id.in_(ids),
        Order.is_estimate == False, Order.status.notin_(list(terminal)))  # noqa: E712
        .group_by(Order.client_id))).all()) if ids else {}
    out = [{
        "client_id": cl.id, "name": cl.name,
        "vehicle_count": dev_counts.get(cl.id, 0),
        "open_tickets": open_counts.get(cl.id, 0),
    } for cl in companies]
    out.sort(key=lambda r: r["vehicle_count"], reverse=True)
    return {"companies": out}


@router.get("/fleet/{client_id}")
async def fleet_detail(
    client_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Per-vehicle service history + a consolidated billing summary for a fleet
    account (B2B: one client, many vehicles, one rolled-up spend figure)."""
    cl = (await db.execute(select(Client).where(
        Client.id == client_id, Client.org_id == current.org_id))).scalar_one_or_none()
    if not cl:
        from fastapi import HTTPException
        raise HTTPException(404, "Client not found")
    terminal = await _terminal_keys(db, current.org_id)
    devices = (await db.execute(select(Device).where(Device.client_id == client_id))).scalars().all()

    # batch every vehicle's orders in one query (was one query per device)
    device_ids = [d.id for d in devices]
    orders_by_dev: dict = {}
    if device_ids:
        all_orders = (await db.execute(select(Order).where(
            Order.org_id == current.org_id, Order.device_id.in_(device_ids),
            Order.is_estimate == False))).scalars().all()  # noqa: E712
        for o in all_orders:
            orders_by_dev.setdefault(o.device_id, []).append(o)

    vehicles, consolidated = [], 0.0
    for d in devices:
        orders = orders_by_dev.get(d.id, [])
        spend = sum(float(o.total or 0) for o in orders if o.status in terminal)
        consolidated += spend
        last = max((o.closed_at for o in orders if o.closed_at), default=None)
        vehicles.append({
            "device_id": d.id,
            "label": " ".join(x for x in [d.brand, d.model] if x) or d.asset_type or "Vehicle",
            "vin": d.vin, "hin": d.hin, "serial": d.serial,
            "service_count": len(orders),
            "lifetime_spend": q2(spend),
            "last_serviced": last.date().isoformat() if last else None,
        })
    vehicles.sort(key=lambda v: v["lifetime_spend"], reverse=True)
    return {"client_id": client_id, "name": cl.name,
            "vehicle_count": len(devices),
            "consolidated_spend": q2(consolidated),
            "vehicles": vehicles}

# ── A9 Business Insights (owner summary) ─────────────────────────────────────
@router.get("/business-insights")
async def business_insights(
    days: int = Query(30, ge=7, le=365),
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Owner-level KPI roll-up: revenue, ticket throughput, avg turnaround,
    top technicians and top services over the window."""
    from collections import defaultdict

    start, _ = _window(days)
    terminal = await _terminal_keys(db, current.org_id)
    orders = (await db.execute(select(Order).where(
        Order.org_id == current.org_id, Order.is_estimate == False,  # noqa: E712
        Order.created_at >= start))).scalars().all()

    created = len(orders)
    closed = [o for o in orders if o.status in terminal]
    revenue = q2(sum(float(o.total or 0) for o in closed))
    # avg turnaround (hours) for closed orders that have both timestamps
    turns = [((o.closed_at - o.created_at).total_seconds() / 3600.0)
             for o in closed if o.closed_at and o.created_at]
    avg_turnaround_h = round(sum(turns) / len(turns), 1) if turns else None
    close_rate = round(100.0 * len(closed) / created, 1) if created else 0.0
    avg_ticket = q2(revenue / len(closed)) if closed else 0.0

    # top technicians by closed-order revenue (via line technician_id)
    tech_rev: dict = defaultdict(float)
    svc_count: dict = defaultdict(int)
    closed_ids = [o.id for o in closed]
    if closed_ids:
        lines = (await db.execute(select(OrderLine).where(
            OrderLine.order_id.in_(closed_ids)))).scalars().all()
        from backend.services.payroll import _line_splits
        for ln in lines:
            # 144aw — split a line's revenue across its technicians (matches payroll).
            for tid, share in _line_splits(ln):
                tech_rev[tid] += float(ln.total or 0) * share
            if ln.line_type == "labour" and ln.description:
                svc_count[ln.description] += 1
    # resolve tech names
    from backend.models import Employee as _Emp
    emps = {e.id: e.name for e in (await db.execute(select(_Emp).where(
        _Emp.org_id == current.org_id))).scalars().all()}
    top_techs = sorted(
        ({"employee_id": k, "name": emps.get(k, "—"), "revenue": q2(v)}
         for k, v in tech_rev.items()), key=lambda r: r["revenue"], reverse=True)[:5]
    top_services = sorted(
        ({"service": k, "count": v} for k, v in svc_count.items()),
        key=lambda r: r["count"], reverse=True)[:5]

    return {
        "period_days": days,
        "orders_created": created,
        "orders_closed": len(closed),
        "close_rate_pct": close_rate,
        "revenue": revenue,
        "avg_ticket_value": avg_ticket,
        "avg_turnaround_hours": avg_turnaround_h,
        "top_technicians": top_techs,
        "top_services": top_services,
    }


# ── A8 Fleet statement (consolidated B2B billing rollup) ─────────────────────
@router.get("/fleet/{client_id}/statement")
async def fleet_statement(
    client_id: str,
    days: int = Query(90, ge=7, le=365),
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """A single billable statement across a fleet account's vehicles: every
    closed order in the window with its vehicle, plus a total — what a B2B
    account manager invoices monthly."""
    cl = (await db.execute(select(Client).where(
        Client.id == client_id, Client.org_id == current.org_id))).scalar_one_or_none()
    if not cl:
        from fastapi import HTTPException
        raise HTTPException(404, "Client not found")
    start, _ = _window(days)
    terminal = await _terminal_keys(db, current.org_id)
    devices = {d.id: d for d in (await db.execute(select(Device).where(
        Device.client_id == client_id))).scalars().all()}
    orders = (await db.execute(select(Order).where(
        Order.org_id == current.org_id, Order.client_id == client_id,
        Order.is_estimate == False, Order.created_at >= start))).scalars().all()  # noqa: E712

    lines, total = [], 0.0
    for o in orders:
        if o.status not in terminal:
            continue
        dev = devices.get(o.device_id)
        amt = float(o.total or 0)
        total += amt
        lines.append({
            "order_id": o.id, "order_number": o.order_number,
            "vehicle": (" ".join(x for x in [dev.brand, dev.model] if x) if dev else None) or "—",
            "closed_at": o.closed_at.date().isoformat() if o.closed_at else None,
            "amount": q2(amt),
        })
    lines.sort(key=lambda r: r["closed_at"] or "")
    return {"client_id": client_id, "name": cl.name, "period_days": days,
            "line_count": len(lines), "total": q2(total), "lines": lines}
