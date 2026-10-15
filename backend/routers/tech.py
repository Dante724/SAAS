"""Technician mobile API.

Aggregates everything the technician PWA dashboard needs in one round-trip
(so it works on flaky mobile connections and caches cleanly offline), plus the
per-employee notification feed.
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.db.database import get_db
from backend.models import (
    Client,
    Device,
    Employee,
    EventFeedItem,
    Invoice,
    NotificationPreference,
    Order,
    OrderComment,
    OrderStatus,
    StaffNotification,
    TimeEntry,
)
from backend.services.auth import get_current_employee, record_scope

router = APIRouter(prefix="/api/tech", tags=["technician"],
                   dependencies=[Depends(record_scope)])   # role record scope on {order_id}/{client_id}


def _card(o: Order) -> dict:
    return {
        "id": o.id,
        "order_number": o.order_number,
        "status": o.status,
        "priority": o.priority,
        "complaint": o.complaint or "",
        "client": o.client.name if o.client else "Walk-in",
        "device": (f"{o.device.brand} {o.device.model}".strip() if o.device else ""),
        "scheduled_for": o.scheduled_for.isoformat() if o.scheduled_for else None,
        "due_at": o.due_at.isoformat() if o.due_at else None,
        "is_estimate": bool(o.is_estimate),
    }


@router.get("/dashboard")
async def dashboard(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Today / upcoming / completed / overdue jobs for the signed-in technician,
    plus quick stats, clock status and the unread notification count."""
    now = datetime.utcnow()
    today_start = datetime(now.year, now.month, now.day)
    today_end = today_start + timedelta(days=1)

    # Terminal status keys for this org (a job is "completed" once terminal/closed).
    terminal_keys = set((await db.execute(
        select(OrderStatus.key).where(
            OrderStatus.org_id == current.org_id, OrderStatus.is_terminal == True)  # noqa: E712
    )).scalars().all())

    mine = (await db.execute(
        select(Order).where(and_(
            Order.org_id == current.org_id,
            Order.assigned_to_id == current.id,
            Order.is_estimate == False,  # noqa: E712
        )).options(selectinload(Order.client), selectinload(Order.device))
        .order_by(Order.scheduled_for.is_(None), Order.scheduled_for, Order.created_at)
    )).scalars().all()

    today, upcoming, completed, overdue = [], [], [], []
    for o in mine:
        done = o.closed_at is not None or o.status in terminal_keys
        if done:
            completed.append(o)
            continue
        if o.due_at and o.due_at < now:
            overdue.append(o)
        sched = o.scheduled_for
        if sched and today_start <= sched < today_end:
            today.append(o)
        elif sched and sched >= today_end:
            upcoming.append(o)
        elif not sched:
            today.append(o)  # unscheduled active work shows under Today

    running = (await db.execute(
        select(TimeEntry).where(and_(
            TimeEntry.org_id == current.org_id,
            TimeEntry.employee_id == current.id,
            TimeEntry.ended_at.is_(None),
        )).order_by(TimeEntry.started_at.desc())
    )).scalars().first()

    unread = (await db.execute(
        select(func.count(StaffNotification.id)).where(and_(
            StaffNotification.employee_id == current.id,
            StaffNotification.read_at.is_(None),
            StaffNotification.dismissed_at.is_(None),  # 144bp: dismissed don't count as unread
        ))
    )).scalar_one()

    return {
        "today": [_card(o) for o in today],
        "upcoming": [_card(o) for o in upcoming],
        "completed": [_card(o) for o in completed[:25]],
        "overdue": [_card(o) for o in overdue],
        "stats": {
            "today": len(today), "upcoming": len(upcoming),
            "completed": len(completed), "overdue": len(overdue),
            "assigned_total": len(mine),
        },
        "clock": {
            "clocked_in": running is not None,
            "category": running.category if running else None,
            "order_id": running.order_id if running else None,
            "since": running.started_at.isoformat() if running else None,
        },
        "unread_notifications": int(unread),
        "server_time": now.isoformat(),
    }


@router.get("/notifications")
async def list_notifications(
    only_unread: bool = False,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    q = select(StaffNotification).where(
        StaffNotification.employee_id == current.id,
        StaffNotification.dismissed_at.is_(None),  # 144bp: hide dismissed from the bell
    )
    if only_unread:
        q = q.where(StaffNotification.read_at.is_(None))
    rows = (await db.execute(q.order_by(StaffNotification.created_at.desc()).limit(50))).scalars().all()
    return {"items": [{
        "id": n.id, "kind": n.kind, "title": n.title, "body": n.body,
        "deep_link": n.deep_link, "read": n.read_at is not None,
        "created_at": n.created_at.isoformat(),
    } for n in rows]}


@router.post("/notifications/{nid}/read")
async def mark_read(
    nid: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    n = (await db.execute(select(StaffNotification).where(
        StaffNotification.id == nid, StaffNotification.employee_id == current.id))).scalar_one_or_none()
    if not n:
        raise HTTPException(404, "Notification not found")
    n.read_at = n.read_at or datetime.utcnow()
    await db.commit()
    return {"ok": True}


@router.post("/notifications/read-all")
async def mark_all_read(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    rows = (await db.execute(select(StaffNotification).where(
        StaffNotification.employee_id == current.id,
        StaffNotification.read_at.is_(None)))).scalars().all()
    now = datetime.utcnow()
    for n in rows:
        n.read_at = now
    await db.commit()
    return {"ok": True, "marked": len(rows)}


@router.post("/notifications/{nid}/dismiss")
async def dismiss_notification(
    nid: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """144bp: soft-dismiss one notification — hidden from the bell, row kept for audit.
    Also marks it read so it never lingers in the unread count."""
    n = (await db.execute(select(StaffNotification).where(
        StaffNotification.id == nid, StaffNotification.employee_id == current.id))).scalar_one_or_none()
    if not n:
        raise HTTPException(404, "Notification not found")
    now = datetime.utcnow()
    n.dismissed_at = n.dismissed_at or now
    n.read_at = n.read_at or now
    await db.commit()
    return {"ok": True}


@router.post("/notifications/dismiss-all")
async def dismiss_all_notifications(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """144bp: clear the bell — soft-dismiss every not-yet-dismissed notification for this user."""
    rows = (await db.execute(select(StaffNotification).where(
        StaffNotification.employee_id == current.id,
        StaffNotification.dismissed_at.is_(None)))).scalars().all()
    now = datetime.utcnow()
    for n in rows:
        n.dismissed_at = now
        n.read_at = n.read_at or now
    await db.commit()
    return {"ok": True, "dismissed": len(rows)}


# ── Notification preferences (Part 9) ─────────────────────────────────────────
from backend.services.push import VALID_KINDS  # noqa: E402
from backend.services.money import q2, q4


@router.get("/preferences")
async def get_preferences(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """All notification kinds with the technician's on/off state (on by default)."""
    rows = (await db.execute(select(NotificationPreference).where(
        NotificationPreference.employee_id == current.id))).scalars().all()
    state = {r.kind: r.enabled for r in rows}
    return {"preferences": [{"kind": k, "enabled": state.get(k, True)} for k in sorted(VALID_KINDS)]}


class PrefIn(BaseModel):
    kind: str
    enabled: bool


@router.put("/preferences")
async def set_preference(
    data: PrefIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    if data.kind not in VALID_KINDS:
        raise HTTPException(422, "Unknown notification kind")
    pref = (await db.execute(select(NotificationPreference).where(
        NotificationPreference.employee_id == current.id,
        NotificationPreference.kind == data.kind))).scalar_one_or_none()
    if pref:
        pref.enabled = data.enabled
    else:
        db.add(NotificationPreference(org_id=current.org_id, employee_id=current.id,
                                      kind=data.kind, enabled=data.enabled))
    await db.commit()
    return {"ok": True, "kind": data.kind, "enabled": data.enabled}


# ── Customer & device history (Part 7) ────────────────────────────────────────
async def _balance(db: AsyncSession, o: Order) -> float:
    from backend.services.order_money import balance_due
    return await balance_due(db, o)


async def _order_history_cards(db: AsyncSession, org_id: str, orders: list[Order]) -> tuple[list, float]:
    cards, outstanding = [], 0.0
    for o in orders:
        bal = await _balance(db, o)
        if o.closed_at is None:
            outstanding += bal
        cards.append({
            "id": o.id, "order_number": o.order_number, "status": o.status,
            "is_estimate": bool(o.is_estimate), "total": o.total, "balance": bal,
            "warranty_until": o.warranty_until.isoformat() if o.warranty_until else None,
            "closed_at": o.closed_at.isoformat() if o.closed_at else None,
            "created_at": o.created_at.isoformat() if o.created_at else None,
        })
    return cards, q2(outstanding)


@router.get("/client/{client_id}/history")
async def client_history(
    client_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Everything a technician needs about a customer at the bench: past repairs,
    invoices, warranty, outstanding balance, recent notes/photos, recommendations."""
    client = (await db.execute(select(Client).where(
        Client.id == client_id, Client.org_id == current.org_id))).scalar_one_or_none()
    if not client:
        raise HTTPException(404, "Client not found")

    orders = (await db.execute(select(Order).where(
        Order.org_id == current.org_id, Order.client_id == client_id)
        .order_by(Order.created_at.desc()))).scalars().all()
    order_ids = [o.id for o in orders]
    cards, outstanding = await _order_history_cards(db, current.org_id, orders)

    invoices = (await db.execute(select(Invoice).where(
        Invoice.org_id == current.org_id, Invoice.client_id == client_id)
        .order_by(Invoice.created_at.desc()))).scalars().all()
    inv_out = [{"id": i.id, "invoice_number": i.invoice_number, "status": i.status,
                "total": i.total, "amount_paid": i.amount_paid,
                "balance": q2((i.total or 0) - (i.amount_paid or 0)),
                "issued_at": i.issued_at.isoformat() if i.issued_at else None} for i in invoices]

    devices = (await db.execute(select(Device).where(
        Device.client_id == client_id))).scalars().all()
    dev_out = [{"id": d.id, "label": f"{d.brand or ''} {d.model or ''}".strip() or d.asset_type,
                "serial": d.serial, "imei": d.imei, "vin": d.vin, "hin": d.hin, "asset_type": d.asset_type} for d in devices]

    feed, recommendations = [], []
    if order_ids:
        feed_rows = (await db.execute(select(EventFeedItem).where(
            EventFeedItem.entity_type == "order", EventFeedItem.entity_id.in_(order_ids))
            .order_by(EventFeedItem.created_at.desc()).limit(20))).scalars().all()
        feed = [{"item_type": f.item_type, "body": f.body,
                 "media_mime": f.media_mime, "created_at": f.created_at.isoformat() if f.created_at else None}
                for f in feed_rows]
        rec_rows = (await db.execute(select(OrderComment).where(
            OrderComment.order_id.in_(order_ids), OrderComment.visible_to_client == True)  # noqa: E712
            .order_by(OrderComment.created_at.desc()).limit(10))).scalars().all()
        recommendations = [{"body": c.body, "created_at": c.created_at.isoformat() if c.created_at else None}
                           for c in rec_rows]

    warranties = [c for c in cards if c["warranty_until"]]
    return {
        "client": {"id": client.id, "name": client.name,
                   "phone": getattr(client, "phone", None), "email": getattr(client, "email", None)},
        "stats": {"orders": len(orders), "outstanding_balance": outstanding,
                  "open_invoices": sum(1 for i in inv_out if i["balance"] > 0)},
        "orders": cards, "invoices": inv_out, "devices": dev_out,
        "warranties": warranties, "recent_activity": feed, "recommendations": recommendations,
    }


@router.get("/device/{device_id}/history")
async def device_history(
    device_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Repair history for a single device/asset."""
    # Build 144do (security): scope to the caller's org (was an unscoped cross-tenant read).
    device = (await db.execute(select(Device).where(
        Device.id == device_id, Device.org_id == current.org_id))).scalar_one_or_none()
    if not device:
        raise HTTPException(404, "Device not found")
    orders = (await db.execute(select(Order).where(
        Order.org_id == current.org_id, Order.device_id == device_id)
        .order_by(Order.created_at.desc()))).scalars().all()
    cards, outstanding = await _order_history_cards(db, current.org_id, orders)
    return {
        "device": {"id": device.id, "label": f"{device.brand or ''} {device.model or ''}".strip() or device.asset_type,
                   "serial": device.serial, "imei": device.imei, "vin": device.vin, "hin": device.hin, "asset_type": device.asset_type, "condition": device.condition},
        "stats": {"repairs": len(orders), "outstanding_balance": outstanding},
        "orders": cards,
        "warranties": [c for c in cards if c["warranty_until"]],
    }
