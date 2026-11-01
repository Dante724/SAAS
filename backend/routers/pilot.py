"""Tekly Go shop pilot: QR job labels and the pilot report (services/pilot.py,
docs/pilot/tekly-go-shop-pilot.md)."""
import asyncio
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer, selectinload

from backend.config import settings
from backend.db.database import get_db
from backend.models import AuditLog, Device, Employee, Order, OrderStatus, Organisation
from backend.services import pilot, scoping
from backend.services.auth import require_perm

router = APIRouter(prefix="/api/pilot", tags=["pilot"])

MAX_LABEL_JOBS = 200
MAX_REPORT_DAYS = 92


def _ids(raw: Optional[str]) -> list[str]:
    return [x.strip() for x in (raw or "").split(",") if x.strip()]


@router.get("/job-labels")
async def job_labels(
    request: Request,
    order_ids: Optional[str] = Query(None, description="comma-separated work order ids"),
    assigned_to_id: Optional[str] = Query(None, description="instead of ids: every open job assigned to this person"),
    paper: str = Query("a4", pattern="^(a4|letter)$"),
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_create_tickets")),
):
    """A PDF sheet of QR labels, one per job, to tape on the tickets. Each QR opens the job in
    Tekly Go. Printing marks those jobs as the pilot's jobs for the report."""
    ids = _ids(order_ids)
    if not ids and not assigned_to_id:
        raise HTTPException(400, "Pick at least one work order")
    if len(ids) > MAX_LABEL_JOBS:
        raise HTTPException(422, f"At most {MAX_LABEL_JOBS} labels at a time")
    role = await scoping.role_for(db, current)
    q = select(Order).where(Order.org_id == current.org_id).options(
        selectinload(Order.client), selectinload(Order.device).options(defer(Device.image_data)))
    if ids:
        found = (await db.execute(q.where(Order.id.in_(ids)))).scalars().all()
        if len(found) != len(set(ids)):
            raise HTTPException(404, "Work order not found")
        visible = {o.id for o in (await db.execute(scoping.scope_orders(
            select(Order).where(Order.org_id == current.org_id, Order.id.in_(ids)), role, current))).scalars().all()}
        if any(o.id not in visible for o in found):
            raise HTTPException(403, "You can only print labels for work orders you can see")
        by_id = {o.id: o for o in found}
        orders = [by_id[i] for i in dict.fromkeys(ids)]          # the order they were picked in
    else:
        if not (await db.execute(select(Employee.id).where(
                Employee.id == assigned_to_id, Employee.org_id == current.org_id))).scalar_one_or_none():
            raise HTTPException(404, "Employee not found")
        closed = select(OrderStatus.key).where(
            OrderStatus.org_id == current.org_id, OrderStatus.record_type == "work_order",
            OrderStatus.status_group.in_(("closed", "dropped")))
        orders = (await db.execute(scoping.scope_orders(q.where(
            Order.assigned_to_id == assigned_to_id, Order.archived == False,  # noqa: E712
            Order.is_estimate == False, Order.status.not_in(closed)), role, current)  # noqa: E712
            .order_by(Order.created_at).limit(MAX_LABEL_JOBS))).scalars().all()
        if not orders:
            raise HTTPException(404, "No open work orders assigned to this person")

    base = (settings.PUBLIC_BASE_URL or "").rstrip("/") or str(request.base_url).rstrip("/")
    labels = [{"url": pilot.job_link(base, o.id), "number": o.order_number or "",
               "name": pilot.first_name(o.client.name if o.client else ""),
               "device": pilot.device_text(o)} for o in orders]
    org_name = (await db.execute(select(Organisation.name).where(
        Organisation.id == current.org_id))).scalar_one_or_none() or ""
    pdf = await asyncio.to_thread(pilot.render_job_labels, labels, paper=paper, org_name=org_name)
    for o in orders:
        db.add(AuditLog(org_id=current.org_id, actor_id=current.id, action=pilot.LABELS,
                        entity_type="order", entity_id=o.id, diff={"number": o.order_number}))
    await db.commit()
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": 'inline; filename="tekly-go-job-labels.pdf"',
                             "Cache-Control": "no-store"})


@router.get("/go-report")
async def go_report(
    date_from: Optional[date] = Query(None, alias="from"),
    date_to: Optional[date] = Query(None, alias="to"),
    order_ids: Optional[str] = Query(None, description="comma-separated; default = jobs that got a pilot label"),
    employee_ids: Optional[str] = Query(None, description="comma-separated; limits the headline (e.g. the techs)"),
    format: str = Query("json", pattern="^(json|csv)$"),
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    """The pilot report: per employee, status changes and photos total vs from Tekly Go, messages
    from Go, scans and counter lookups by kind; plus the share of status changes made on a phone
    against the 50% decision rule. Owners and admins only."""
    end = date_to or date.today()
    start = date_from or (end - timedelta(days=6))
    if start > end:
        raise HTTPException(422, "from must be on or before to")
    if (end - start).days >= MAX_REPORT_DAYS:
        raise HTTPException(422, f"At most {MAX_REPORT_DAYS} days at a time")
    oids = _ids(order_ids) if order_ids is not None else None
    if oids is not None:
        found = set((await db.execute(select(Order.id).where(
            Order.org_id == current.org_id, Order.id.in_(oids)))).scalars().all()) if oids else set()
        if found != set(oids):
            raise HTTPException(404, "Work order not found")
    eids = _ids(employee_ids) or None
    if eids:
        found = set((await db.execute(select(Employee.id).where(
            Employee.org_id == current.org_id, Employee.id.in_(eids)))).scalars().all())
        if found != set(eids):
            raise HTTPException(404, "Employee not found")
    rep = await pilot.go_report(db, current.org_id, start, end, order_ids=oids, employee_ids=eids)
    if format == "csv":
        return Response(content=pilot.report_csv(rep), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="tekly-go-pilot-{start}-{end}.csv"',
                                 "Cache-Control": "no-store"})
    return rep
