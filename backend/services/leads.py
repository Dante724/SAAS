"""WS-C — inquiries (leads): one place to create, update and convert them, and to turn a
missed call into one. The staff web router, the public API v2 and Tekly Go all go through here,
so the rules (tenant checks, respond-by times, custom fields, the timeline) are the same
everywhere.

Missed calls (automation key ``lead_on_missed_call``, on unless a shop turns it off):
  * the caller is matched to a contact by phone (same rule as the call log);
  * a repeat call from the same number while an inquiry is still open, within
    ``merge_hours`` (24 by default), is added to that inquiry instead of opening a new one;
  * one "Call back" task per inquiry, assigned to someone on duty;
  * everyone clocked in right now is alerted; with nobody on duty, the contact's manager,
    else the shop's admins.
Push/log titles stay generic: names and numbers only go in the stored notification body.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    AutomationSetting,
    Client,
    Device,
    Employee,
    Lead,
    LeadType,
    Location,
    Order,
    OrderStatus,
    OrderStatusLog,
    Shift,
    Task,
)

log = logging.getLogger("tekly.leads")

STATUSES = ["new", "in_progress", "won", "lost"]
LABELS = {"new": "New", "in_progress": "In progress", "won": "Won", "lost": "Lost"}
OPEN = ("new", "in_progress")
SOURCES = ("phone", "walk_in", "whatsapp", "sms", "email", "instagram", "facebook", "web", "api", "other")

MISSED_KEY = "lead_on_missed_call"
ALERT_QUIET_MIN = 5   # repeat calls to one inquiry within this many minutes don't alert again
MISSED_DEFAULTS = {"merge_hours": 24, "sla_minutes": 30, "notify": True, "fallback_employee_id": None}
FIELDS = ("name", "phone", "email", "source", "inquiry", "manager_id", "lead_type_id",
          "device_brand", "device_model", "serial", "status")


async def own(db: AsyncSession, model, obj_id: Optional[str], org_id: str, what: str):
    """The row if it belongs to this shop, else 404 (never reveal another shop's ids)."""
    if not obj_id:
        return None
    row = (await db.execute(select(model).where(model.id == obj_id, model.org_id == org_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, f"{what} not found")
    return row


def ser(l: Lead, *, order_number: Optional[str] = None, type_name: Optional[str] = None) -> dict:
    overdue = bool(l.sla_due_at and l.status in OPEN and l.sla_due_at < datetime.utcnow())
    return {
        "id": l.id, "name": l.name, "phone": l.phone, "email": l.email, "source": l.source,
        "inquiry": l.inquiry, "status": l.status, "client_id": l.client_id,
        "manager_id": l.manager_id, "location_id": l.location_id,
        "lead_type_id": l.lead_type_id, "lead_type": type_name,
        "device_brand": l.device_brand, "device_model": l.device_model, "serial": l.serial,
        "custom_data": l.custom_data or {},
        "call_count": l.call_count or 0,
        "last_call_at": l.last_call_at.isoformat() if l.last_call_at else None,
        "converted_order_id": l.converted_order_id, "converted_order_number": order_number,
        "sla_due_at": l.sla_due_at.isoformat() if l.sla_due_at else None,
        "sla_overdue": overdue,
        "created_at": l.created_at.isoformat() if l.created_at else None,
        "updated_at": l.updated_at.isoformat() if l.updated_at else None,
    }


async def ser_many(db: AsyncSession, rows: list[Lead]) -> list[dict]:
    """Serialize with converted job numbers and type names resolved in two queries."""
    oids = [l.converted_order_id for l in rows if l.converted_order_id]
    tids = [l.lead_type_id for l in rows if l.lead_type_id]
    nums = dict((await db.execute(select(Order.id, Order.order_number).where(Order.id.in_(oids)))).all()) if oids else {}
    names = dict((await db.execute(select(LeadType.id, LeadType.name).where(LeadType.id.in_(tids)))).all()) if tids else {}
    return [ser(l, order_number=nums.get(l.converted_order_id), type_name=names.get(l.lead_type_id)) for l in rows]


async def ser_one(db: AsyncSession, l: Lead) -> dict:
    return (await ser_many(db, [l]))[0]


async def _feed(db, org_id: str, lead_id: str, message: str) -> None:
    from backend.routers.feed import log_system_event
    await log_system_event(db, org_id, "lead", lead_id, message)


async def _emit(db, org_id: str, event: str, lead: Lead) -> None:
    from backend.services.webhooks import emit_event
    await emit_event(db, org_id, event, {"id": lead.id, "status": lead.status, "source": lead.source,
                                         "client_id": lead.client_id})


def _clean(v, n: int) -> Optional[str]:
    v = (v or "").strip() if isinstance(v, str) else v
    return (v[:n] if isinstance(v, str) else v) or None


async def _default_location(db, org_id: str) -> Optional[str]:
    return (await db.execute(select(Location.id).where(Location.org_id == org_id)
                             .order_by(Location.id).limit(1))).scalar_one_or_none()


async def create_lead(db: AsyncSession, org_id: str, data: dict, *, actor: Optional[Employee] = None,
                      idempotency_key: Optional[str] = None, via: str = "staff") -> tuple[Lead, bool]:
    """Create an inquiry. Returns (lead, created). With an idempotency key, a retry returns the
    inquiry the first attempt created (created=False) instead of a duplicate. Commits."""
    from backend.services.call_contacts import find_client
    key = _clean(idempotency_key, 64)
    if key:
        prior = (await db.execute(select(Lead).where(Lead.org_id == org_id, Lead.idempotency_key == key))).scalar_one_or_none()
        if prior:
            return prior, False
    name = _clean(data.get("name"), 200)
    if not name:
        raise HTTPException(422, "Enter the customer's name")
    source = data.get("source") or "phone"
    if source not in SOURCES:
        raise HTTPException(422, "Unknown channel")
    ltype = await own(db, LeadType, data.get("lead_type_id"), org_id, "Inquiry type")
    if ltype and not ltype.active:
        raise HTTPException(422, "That inquiry type is switched off")
    manager = await own(db, Employee, data.get("manager_id"), org_id, "Employee")
    client = await own(db, Client, data.get("client_id"), org_id, "Contact")
    phone = _clean(data.get("phone"), 40)
    if not client and phone:
        client = await find_client(db, org_id, phone)
    from backend.routers.forms import resolve_custom_data
    custom = await resolve_custom_data(db, org_id, "lead", data.get("custom_data"), creating=True)
    sla = ltype.sla_minutes if ltype else int(data.get("sla_minutes") or 60)
    lead = Lead(org_id=org_id,
                location_id=(actor.location_id if actor and actor.location_id else await _default_location(db, org_id)),
                name=name, phone=phone, email=_clean(data.get("email"), 200), source=source,
                inquiry=_clean(data.get("inquiry"), 4000), status="new",
                client_id=client.id if client else None,
                manager_id=(manager.id if manager else (actor.id if actor else None)),
                lead_type_id=ltype.id if ltype else None,
                device_brand=_clean(data.get("device_brand"), 100), device_model=_clean(data.get("device_model"), 100),
                serial=_clean(data.get("serial"), 100), custom_data=custom,
                idempotency_key=key, sla_due_at=datetime.utcnow() + timedelta(minutes=max(sla, 1)))
    db.add(lead)
    try:
        await db.flush()
    except IntegrityError:          # a concurrent retry with the same key won the insert
        await db.rollback()
        prior = (await db.execute(select(Lead).where(Lead.org_id == org_id, Lead.idempotency_key == key))).scalar_one_or_none() if key else None
        if prior is None:
            raise
        return prior, False
    who = actor.name if actor else ("the API" if via == "api" else "Tekly")
    await _feed(db, org_id, lead.id, f"Inquiry logged by {who}" + (f" ({ltype.name})" if ltype else ""))
    await _emit(db, org_id, "inquiry.created", lead)
    await db.commit()
    await db.refresh(lead)
    return lead, True


async def update_lead(db: AsyncSession, org_id: str, lead: Lead, payload: dict,
                      *, actor: Optional[Employee] = None) -> Lead:
    """Apply a partial update. Every referenced id must be this shop's. Commits."""
    payload = {k: v for k, v in payload.items() if k in FIELDS or k == "custom_data"}
    if "status" in payload and payload["status"] not in STATUSES:
        raise HTTPException(422, f"status must be one of {STATUSES}")
    if payload.get("status") == "won" and not lead.converted_order_id:
        raise HTTPException(422, "Convert the inquiry to a job to win it")
    if "status" in payload and payload["status"] != lead.status and lead.converted_order_id:
        raise HTTPException(409, "This inquiry is already a job")
    if "source" in payload and payload["source"] not in SOURCES:
        raise HTTPException(422, "Unknown channel")
    if "name" in payload and not _clean(payload["name"], 200):
        raise HTTPException(422, "Enter the customer's name")
    if payload.get("manager_id"):
        await own(db, Employee, payload["manager_id"], org_id, "Employee")
    if payload.get("lead_type_id") and payload["lead_type_id"] != lead.lead_type_id:
        t = await own(db, LeadType, payload["lead_type_id"], org_id, "Inquiry type")
        if not t.active:
            raise HTTPException(422, "That inquiry type is switched off")
        if lead.status in OPEN and lead.created_at:     # the respond-by time follows the new type
            lead.sla_due_at = lead.created_at + timedelta(minutes=max(t.sla_minutes, 1))
    if "custom_data" in payload:
        from backend.routers.forms import resolve_custom_data
        lead.custom_data = await resolve_custom_data(db, org_id, "lead", payload.pop("custom_data"),
                                                     existing=lead.custom_data or {})
    before = lead.status
    limits = {"name": 200, "phone": 40, "email": 200, "inquiry": 4000, "device_brand": 100,
              "device_model": 100, "serial": 100}
    for k, v in payload.items():
        setattr(lead, k, _clean(v, limits[k]) if k in limits else (v or None if k.endswith("_id") else v))
    if lead.status != before:
        who = actor.name if actor else "the API"
        await _feed(db, org_id, lead.id, f"Moved from {LABELS.get(before, before)} to {LABELS[lead.status]} by {who}")
    lead.updated_at = datetime.utcnow()
    await _emit(db, org_id, "inquiry.updated", lead)
    await db.commit()
    await db.refresh(lead)
    return lead


async def convert_lead(db: AsyncSession, org_id: str, lead: Lead, *, actor: Optional[Employee] = None,
                       is_estimate: bool = False) -> dict:
    """Win the inquiry: link (or create) the contact, attach the device, open the job (or
    estimate) at its default status. Commits."""
    from backend.services.call_contacts import find_client
    from backend.services.numbering import next_number
    if lead.converted_order_id:
        raise HTTPException(409, "This inquiry is already a job")
    if lead.status == "lost":
        raise HTTPException(409, "Reopen the inquiry before converting it")
    client = await own(db, Client, lead.client_id, org_id, "Contact") if lead.client_id else None
    if not client and lead.phone:
        client = await find_client(db, org_id, lead.phone)
    if not client:
        client = Client(org_id=org_id, name=lead.name, phone=lead.phone, email=lead.email,
                        client_type="person", manager_id=lead.manager_id)
        db.add(client)
        await db.flush()
    lead.client_id = client.id

    device_id = None
    if lead.device_brand or lead.device_model or lead.serial:
        from backend.services import identifiers as ids
        kind = ids.guess_kind(lead.serial) if lead.serial else "serial"
        ident = {"imei": None, "serial": None}
        if lead.serial:
            ident["imei" if kind == "imei" else "serial"] = ids.normalize("imei" if kind == "imei" else "serial", lead.serial)
        dev = None
        val = ident["imei"] or ident["serial"]
        if val:
            # only this customer's own device: the same IMEI under another contact is not theirs
            dev = (await db.execute(select(Device).where(Device.org_id == org_id, Device.client_id == client.id,
                                                         (Device.imei == val) | (Device.serial == val))
                                    .order_by(Device.id).limit(1))).scalar_one_or_none()
        if dev is None:
            dev = Device(org_id=org_id, client_id=client.id, owner_type="client", asset_type="Device",
                         brand=lead.device_brand, model=lead.device_model,
                         serial=ident["serial"], imei=ident["imei"])
            db.add(dev)
            await db.flush()
        device_id = dev.id

    init = (await db.execute(select(OrderStatus).where(
        OrderStatus.org_id == org_id,
        OrderStatus.record_type == ("estimate" if is_estimate else "work_order"),
        OrderStatus.is_default == True, OrderStatus.active == True))).scalars().first()  # noqa: E712
    location_id = lead.location_id or (actor.location_id if actor else None) or await _default_location(db, org_id)
    if not location_id:
        raise HTTPException(409, "Add a location before converting inquiries")
    order = Order(org_id=org_id, location_id=location_id, client_id=client.id, device_id=device_id,
                  order_number=await next_number(db, org_id, "order"),
                  status=(init.key if init else ("est_draft" if is_estimate else "new")), is_estimate=is_estimate,
                  complaint=lead.inquiry or f"Inquiry from {lead.name}",
                  assigned_to_id=lead.manager_id or (actor.id if actor else None))
    db.add(order)
    await db.flush()
    db.add(OrderStatusLog(order_id=order.id, from_status=None, to_status=order.status,
                          changed_by_id=actor.id if actor else None,
                          note=f"Created from inquiry ({lead.source})"))
    lead.status = "won"
    lead.converted_order_id = order.id
    lead.updated_at = datetime.utcnow()
    from backend.routers.feed import log_system_event
    await log_system_event(db, org_id, "order", order.id, f"Created from inquiry: {lead.name} ({lead.source})")
    await _feed(db, org_id, lead.id, f"Converted to {'estimate' if is_estimate else 'job'} {order.order_number}")
    # Any open call-back task for this inquiry is done: the customer is now a job.
    for t in (await db.execute(select(Task).where(Task.org_id == org_id, Task.lead_id == lead.id,
                                                  Task.status != "done"))).scalars().all():
        t.order_id = t.order_id or order.id
        t.status, t.completed_at = "done", datetime.utcnow()
    await _emit(db, org_id, "inquiry.converted", lead)
    if not is_estimate:   # same as a job made on the work-order screen (estimates don't emit it)
        from backend.services.webhooks import emit_event
        await emit_event(db, org_id, "order.created", {"id": order.id, "order_number": order.order_number,
                                                       "status": order.status, "source": "inquiry"})
    await db.commit()
    return {"ok": True, "order_id": order.id, "order_number": order.order_number,
            "client_id": client.id, "device_id": device_id}


# ── Missed call → inquiry ────────────────────────────────────────────────────
async def missed_settings(db: AsyncSession, org_id: str) -> dict:
    s = (await db.execute(select(AutomationSetting).where(
        AutomationSetting.org_id == org_id, AutomationSetting.key == MISSED_KEY))).scalar_one_or_none()
    cfg = {**MISSED_DEFAULTS, **((s.config if s and isinstance(s.config, dict) else {}) or {})}
    return {"enabled": True if s is None else bool(s.enabled), "config": cfg}


async def on_duty(db: AsyncSession, org_id: str) -> list[Employee]:
    """Active employees clocked in (open attendance shift) at one of this shop's locations."""
    locs = select(Location.id).where(Location.org_id == org_id)
    return list((await db.execute(
        select(Employee).join(Shift, Shift.employee_id == Employee.id)
        .where(Employee.org_id == org_id, Employee.active == True,  # noqa: E712
               Shift.ended_at.is_(None), Shift.location_id.in_(locs))
        .order_by(Shift.started_at).distinct())).scalars().all())


async def _admins(db: AsyncSession, org_id: str) -> list[Employee]:
    from backend.services.auth import has_perm
    staff = (await db.execute(select(Employee).where(Employee.org_id == org_id, Employee.active == True)  # noqa: E712
                              .order_by(Employee.created_at))).scalars().all()
    return [e for e in staff if await has_perm(db, e, "can_manage_settings")]


async def _recipients(db, org_id: str, client: Optional[Client], cfg: dict) -> tuple[list[Employee], bool]:
    """(who to alert, whether they're on duty). Every id is re-checked against the shop."""
    duty = await on_duty(db, org_id)
    if duty:
        return duty, True
    for eid in (client.manager_id if client else None, cfg.get("fallback_employee_id")):
        if eid:
            e = (await db.execute(select(Employee).where(Employee.id == eid, Employee.org_id == org_id,
                                                         Employee.active == True))).scalar_one_or_none()  # noqa: E712
            if e:
                return [e], False
    return await _admins(db, org_id), False


async def on_missed_call(db: AsyncSession, org_id: str, call) -> Optional[dict]:
    """Open (or add to) an inquiry for a missed inbound call, make sure it has a call-back task
    and alert whoever is on duty. Returns {"lead_id", "task_id", "merged"} or None when the
    automation is off or the caller is anonymous. Commits."""
    from backend.services import call_contacts as cc
    st = await missed_settings(db, org_id)
    number = (call.from_number or "").strip()
    if not st["enabled"] or len(cc.digits(number)) < 4:
        return None
    cfg = st["config"]
    now = datetime.utcnow()
    try:
        raw = cfg.get("merge_hours")
        merge_hours = max(0, min(int(MISSED_DEFAULTS["merge_hours"] if raw is None else raw), 24 * 14))
    except (TypeError, ValueError):
        merge_hours = MISSED_DEFAULTS["merge_hours"]
    client = await own(db, Client, call.client_id, org_id, "Contact") if call.client_id else await cc.find_client(db, org_id, number)

    lead = None
    if merge_hours:
        since = now - timedelta(hours=merge_hours)
        candidates = (await db.execute(select(Lead).where(
            Lead.org_id == org_id, Lead.status.in_(OPEN), Lead.phone.is_not(None),
            or_(Lead.created_at >= since, Lead.last_call_at >= since))
            .order_by(Lead.created_at.desc()).limit(500))).scalars().all()
        lead = next((l for l in candidates if cc.same_number(l.phone, number)
                     and (l.last_call_at or l.created_at) >= since), None)
    name = client.name if client else cc.format_phone(number)
    merged = lead is not None
    quiet = False                     # a burst of repeat calls alerts once, not once per call
    if lead:
        quiet = bool(lead.last_call_at and now - lead.last_call_at < timedelta(minutes=ALERT_QUIET_MIN))
        lead.call_count = (lead.call_count or 0) + 1
        lead.last_call_at = now
        lead.updated_at = now
        if client and not lead.client_id:
            lead.client_id = client.id
        await _feed(db, org_id, lead.id, "Missed a call from this customer" if lead.call_count == 1
                    else f"Missed another call ({lead.call_count} in total)")
    else:
        try:
            sla = max(1, int(cfg.get("sla_minutes") or 30))
        except (TypeError, ValueError):
            sla = MISSED_DEFAULTS["sla_minutes"]
        lead = Lead(org_id=org_id, location_id=await _default_location(db, org_id), name=name[:200],
                    phone=number[:40], email=client.email if client else None, source="phone",
                    inquiry="Missed call: call them back", status="new",
                    client_id=client.id if client else None,
                    manager_id=client.manager_id if client else None,
                    call_count=1, last_call_at=now, custom_data={},
                    sla_due_at=now + timedelta(minutes=sla))
        db.add(lead)
        await db.flush()
        await _feed(db, org_id, lead.id, "Inquiry opened from a missed call")
        await _emit(db, org_id, "inquiry.created", lead)

    # The older task-only automation's settings still shape the call-back task when it's on.
    legacy = (await db.execute(select(AutomationSetting).where(
        AutomationSetting.org_id == org_id, AutomationSetting.key == "task_on_missed_call"))).scalar_one_or_none()
    legacy_cfg = (legacy.config if legacy and legacy.enabled and isinstance(legacy.config, dict) else {}) or {}
    if legacy_cfg.get("assign_to") and not cfg.get("fallback_employee_id"):
        cfg = {**cfg, "fallback_employee_id": legacy_cfg["assign_to"]}
    priority = str(legacy_cfg.get("priority") or "high")[:20]
    people, duty = await _recipients(db, org_id, client, cfg)
    task = (await db.execute(select(Task).where(Task.org_id == org_id, Task.lead_id == lead.id,
                                                Task.status != "done").limit(1))).scalar_one_or_none()
    if task is None:
        assignee = None
        if client and client.manager_id and any(p.id == client.manager_id for p in people):
            assignee = client.manager_id
        elif people:
            assignee = people[0].id
        task = Task(org_id=org_id, title=f"Call back {name}"[:500],
                    description=f"Missed call from {number}. Return the call.",
                    assigned_to_id=assignee, client_id=lead.client_id, lead_id=lead.id,
                    location_id=lead.location_id, priority=priority, status="todo",
                    due_at=lead.sla_due_at)
        db.add(task)
    else:
        task.description = f"Missed call from {number} ({lead.call_count} calls). Return the call."
    await db.commit()
    await db.refresh(task)

    if cfg.get("notify", True) and not quiet:
        from backend.services.push import notify
        body = f"{name} ({number})" + (f": {lead.call_count} missed calls" if lead.call_count > 1 else "")
        for p in people:
            try:
                await notify(db, org_id=org_id, employee_id=p.id, kind="missed_call",
                             title="Missed call: call back needed", body=body,
                             deep_link=f"/inquiries?lead={lead.id}")
            except Exception:   # an alert must never undo the inquiry
                log.warning("missed-call alert failed for an employee", exc_info=True)
    log.info("missed call → inquiry lead=%s merged=%s on_duty=%s alerted=%d", lead.id, merged, duty, len(people))
    return {"lead_id": lead.id, "task_id": task.id, "merged": merged}
