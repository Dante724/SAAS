"""
Notification templates + triggers.

Templates carry a body with {variables}. When a work order changes status
(or is created), matching active templates render and 'send'. Since there's
no real SMS/email gateway in this build, a sent notification is logged to the
work order's event feed as a system event — so you can see exactly what would
have gone out.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional
from backend.db.database import get_db
from backend.models import NotificationTemplate, Employee, Order
from backend.services.auth import get_current_employee, require_perm
from backend.services.message_vars import SHORT_VARS, TOKENS
from backend.services.dates import now_utc

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

VALID_CHANNELS = {"sms", "email", "whatsapp"}
VALID_EVENTS = {"status_change", "order_created", "comment_added"}

# Short variables a template author can use: services/message_vars renders them on every send.
AVAILABLE_VARS = TOKENS


class TemplateIn(BaseModel):
    name: Optional[str] = None
    channel: str = "sms"
    trigger_event: str = "status_change"
    trigger_status: Optional[str] = None
    subject: Optional[str] = None
    body_template: str
    active: bool = True


def _ser(t: NotificationTemplate) -> dict:
    return {
        "id": t.id, "name": t.name, "channel": t.channel,
        "trigger_event": t.trigger_event, "trigger_status": t.trigger_status,
        "subject": t.subject, "body_template": t.body_template, "active": t.active,
    }


@router.get("/templates")
async def list_templates(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    result = await db.execute(
        select(NotificationTemplate)
        .where(NotificationTemplate.org_id == current.org_id)
        .order_by(NotificationTemplate.trigger_event)
    )
    return {"items": [_ser(t) for t in result.scalars().all()], "variables": AVAILABLE_VARS}


@router.post("/templates", status_code=201)
async def create_template(
    data: TemplateIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    if data.channel not in VALID_CHANNELS:
        raise HTTPException(400, "Invalid channel")
    if data.trigger_event not in VALID_EVENTS:
        raise HTTPException(400, "Invalid trigger event")
    t = NotificationTemplate(
        org_id=current.org_id, name=data.name, channel=data.channel,
        trigger_event=data.trigger_event, trigger_status=data.trigger_status,
        subject=data.subject, body_template=data.body_template, active=data.active,
    )
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return _ser(t)


@router.put("/templates/{template_id}")
async def update_template(
    template_id: str, data: TemplateIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    t = (await db.execute(
        select(NotificationTemplate).where(
            NotificationTemplate.id == template_id,
            NotificationTemplate.org_id == current.org_id)
    )).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Template not found")
    t.name = data.name
    t.channel = data.channel
    t.trigger_event = data.trigger_event
    t.trigger_status = data.trigger_status
    t.subject = data.subject
    t.body_template = data.body_template
    t.active = data.active
    await db.commit()
    return _ser(t)


@router.delete("/templates/{template_id}")
async def delete_template(
    template_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    t = (await db.execute(
        select(NotificationTemplate).where(
            NotificationTemplate.id == template_id,
            NotificationTemplate.org_id == current.org_id)
    )).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Template not found")
    await db.delete(t)
    await db.commit()
    return {"ok": True}


# ── rendering + firing ───────────────────────────────

def render_template(body: str, ctx: dict) -> str:
    from backend.services.message_vars import apply
    return apply(body, ctx)


async def fire_notifications(db: AsyncSession, org_id: str, event: str,
                             order: Order, *, shop_name: str = None,
                             client_name: str = None, device: str = None,
                             technician: str = None, new_status: str = None):
    """Find matching active templates, render, and log each to the event feed."""
    from backend.routers.feed import log_system_event
    from backend.services.message_vars import order_values

    q = select(NotificationTemplate).where(
        NotificationTemplate.org_id == org_id,
        NotificationTemplate.active == True,            # noqa: E712
        NotificationTemplate.trigger_event == event,
    )
    templates = (await db.execute(q)).scalars().all()
    if not templates:
        return 0

    fired = 0
    for t in templates:
        # for status_change templates with a specific status filter, must match
        if event == "status_change" and t.trigger_status and t.trigger_status != new_status:
            continue
        # {total} / {balance_due} format in the order's currency with cents (order_values).
        ctx = await order_values(db, org_id, order, t.body_template)
        overrides = {"client_name": client_name, "device": device, "technician": technician,
                     "shop_name": shop_name,
                     "status": (new_status or "").replace("_", " ") if new_status else None}
        ctx.update({k: v for k, v in overrides.items() if v and k in ctx})
        msg = render_template(t.body_template, ctx)
        label = (t.channel or "sms").upper()
        await log_system_event(
            db, org_id, "order", order.id,
            f"Notification sent ({label}): {msg}"
        )
        fired += 1
    return fired


# ── Notification delivery log (communication history) ──
@router.get("/log")
async def notification_log(
    order_id: Optional[str] = None,
    client_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    from backend.models import NotificationLog
    q = select(NotificationLog).where(NotificationLog.org_id == current.org_id)
    if order_id:
        q = q.where(NotificationLog.order_id == order_id)
    if client_id:
        q = q.where(NotificationLog.client_id == client_id)
    rows = (await db.execute(q.order_by(NotificationLog.created_at.desc()).limit(200))).scalars().all()
    return {"items": [{
        "id": r.id, "scope": r.scope, "channel": r.channel, "trigger_event": r.trigger_event,
        "order_id": r.order_id, "client_id": r.client_id, "recipient": r.recipient,
        "subject": r.subject, "body": r.body, "status": r.status, "error": r.error,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows]}


# ── Build 80: notification rules engine ──────────────────────────────────────

from backend.models import NotificationRule  # noqa: E402
from backend.services import notification_rules as _rules  # noqa: E402

MODULES = ["order", "lead", "appointment", "task", "contact"]  # 144ja adds contact lifecycle
EVENTS = ["created", "status_changed", "closed", "reminder",
          "assigned", "changed", "completed",
          "birthday",                          # 144ja: contact-lifecycle event
          "comment_added", "payment_received"]  # 144jc: now real rules events


def _ser_rule(r: NotificationRule) -> dict:
    return {"id": r.id, "module": r.module, "event": r.event,
            "scope": r.scope or "client", "record_type": r.record_type,
            "trigger_status_key": r.trigger_status_key, "channel": r.channel,
            "template_id": r.template_id, "delay_min": r.delay_min,
            "working_hours_only": r.working_hours_only,
            "chat_first": r.chat_first,
            "reminder_offset_min": r.reminder_offset_min,
            "location_id": r.location_id, "active": r.active}


@router.get("/rules")
async def list_rules(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    rows = (await db.execute(select(NotificationRule).where(
        NotificationRule.org_id == current.org_id))).scalars().all()
    return {"items": [_ser_rule(r) for r in rows], "modules": MODULES, "events": EVENTS}


class RuleIn(BaseModel):
    module: str = "order"
    event: str
    scope: str = "client"               # 144jb: client (external) | staff (internal)
    record_type: Optional[str] = None   # 144jb: work_order|estimate|None (order module only)
    trigger_status_key: Optional[str] = None
    channel: str = "email"
    template_id: Optional[str] = None
    delay_min: int = 0
    working_hours_only: bool = False
    chat_first: bool = False
    reminder_offset_min: int = 0
    location_id: Optional[str] = None
    active: bool = True


class RulePatch(BaseModel):
    """Full-edit an existing rule. Every field optional — only supplied keys change.
    (Orderry only lets you swap template/channel inline; Tekly allows a full edit.)"""
    module: Optional[str] = None
    event: Optional[str] = None
    scope: Optional[str] = None
    record_type: Optional[str] = None
    trigger_status_key: Optional[str] = None
    channel: Optional[str] = None
    template_id: Optional[str] = None
    delay_min: Optional[int] = None
    working_hours_only: Optional[bool] = None
    chat_first: Optional[bool] = None
    reminder_offset_min: Optional[int] = None
    location_id: Optional[str] = None
    active: Optional[bool] = None


@router.post("/rules", status_code=201)
async def create_rule(
    data: RuleIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    if data.event not in EVENTS:
        raise HTTPException(400, f"event must be one of {EVENTS}")
    if data.module not in MODULES:
        raise HTTPException(400, f"module must be one of {MODULES}")
    if data.event == "reminder" and data.reminder_offset_min <= 0:
        raise HTTPException(400, "A reminder rule needs reminder_offset_min > 0")
    if data.delay_min < 0:
        raise HTTPException(400, "delay_min can't be negative")
    r = NotificationRule(org_id=current.org_id, **data.model_dump())
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return _ser_rule(r)


@router.patch("/rules/{rule_id}")
async def update_rule(
    rule_id: str,
    data: RulePatch,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    r = (await db.execute(select(NotificationRule).where(
        NotificationRule.id == rule_id,
        NotificationRule.org_id == current.org_id))).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Rule not found")
    fields = data.model_dump(exclude_unset=True)
    if "event" in fields and fields["event"] not in EVENTS:
        raise HTTPException(400, f"event must be one of {EVENTS}")
    if "module" in fields and fields["module"] not in MODULES:
        raise HTTPException(400, f"module must be one of {MODULES}")
    if "delay_min" in fields and fields["delay_min"] is not None and fields["delay_min"] < 0:
        raise HTTPException(400, "delay_min can't be negative")
    # A reminder rule always needs a positive offset (whether it started as one or is becoming one)
    ev = fields.get("event", r.event)
    off = fields.get("reminder_offset_min", r.reminder_offset_min)
    if ev == "reminder" and (off or 0) <= 0:
        raise HTTPException(400, "A reminder rule needs reminder_offset_min > 0")
    for k, v in fields.items():
        setattr(r, k, v)
    await db.commit()
    await db.refresh(r)
    return _ser_rule(r)


@router.delete("/rules/{rule_id}")
async def delete_rule(
    rule_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    r = (await db.execute(select(NotificationRule).where(
        NotificationRule.id == rule_id, NotificationRule.org_id == current.org_id))).scalar_one_or_none()
    if r:
        await db.delete(r)
        await db.commit()
    return {"ok": True}


@router.post("/rules/queue-reminders")
async def queue_reminders(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    """Scan upcoming work orders AND appointments, queueing reminders now within lead time."""
    n = await _rules.queue_reminders(db, org_id=current.org_id)
    b = await _rules.queue_appointment_reminders(db, org_id=current.org_id)
    return {"ok": True, "queued": n + b, "orders": n, "appointments": b}


@router.post("/rules/run-due")
async def run_due(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    """Worker tick: dispatch scheduled notifications whose send time has passed."""
    return await _rules.run_due(db, org_id=current.org_id)


@router.get("/providers")
async def provider_status(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """N-GAP-4 — which delivery channels are live vs simulated, so the shop knows
    whether messages actually go out. Live = provider credentials configured
    (env), or, for email, a connected Gmail/Outlook account for this org."""
    from backend.config import settings
    email_live = bool(settings.SMTP_HOST and (settings.SMTP_FROM or settings.SMTP_USER))
    try:
        from backend.models import EmailConnection
        if not email_live:
            email_live = bool((await db.execute(select(EmailConnection).where(
                EmailConnection.org_id == current.org_id,
                EmailConnection.connected == True))).scalars().first())  # noqa: E712
    except Exception:
        pass
    twilio = bool(getattr(settings, "TWILIO_ACCOUNT_SID", "") and getattr(settings, "TWILIO_AUTH_TOKEN", ""))
    push_live = bool(getattr(settings, "VAPID_PRIVATE_KEY", "") and getattr(settings, "VAPID_PUBLIC_KEY", ""))
    def _s(live): return "live" if live else "simulated"
    return {"channels": {
        "email": _s(email_live),
        "sms": _s(twilio),
        "whatsapp": _s(twilio and bool(getattr(settings, "TWILIO_WHATSAPP_FROM", ""))),
        "push": _s(push_live),
    }}


# 144ja (N-P3) — which variable groups are meaningful per module, like Orderry's
# module-scoped picker (Inquiries shows Lead vars, Workflows shows Order vars, …).
# A module absent here (or module=None) gets the full catalog.
MODULE_VAR_GROUPS = {
    "order":   None,  # Workflows — everything applies
    "lead":    ["Company", "Legal entity", "Client", "Asset", "Employees",
                "Date", "Links", "Other", "Custom fields"],
    "appointment": ["Company", "Legal entity", "Client", "Date", "Links", "Other", "Custom fields"],
    "task":    ["Company", "Employees", "Date", "Other"],  # internal / staff-facing
    "contact": ["Message", "Company", "Legal entity", "Client", "Date", "Links", "Other", "Custom fields"],
}


class TestSendIn(BaseModel):
    channel: str = "email"      # email|sms|whatsapp
    subject: Optional[str] = None
    body: str
    sample: bool = False        # fill the placeholders from the shop's latest job


@router.post("/test")
async def send_test(
    data: TestSendIn,
    db: AsyncSession = Depends(get_db),
    # Same gate as editing the messages: a sample is filled from a real customer's job.
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    """144jc — send a test message to the signed-in user, like Orderry's 'Send test'.
    Variables are sent literally (not resolved) so you can check delivery + formatting."""
    from backend.services.notifications import _adapter_for, notif_creds
    recipient = current.email if data.channel == "email" else getattr(current, "phone", None)
    if not recipient:
        what = "email address" if data.channel == "email" else "mobile number"
        raise HTTPException(400, f"Add a {what} to your profile to get a sample")
    subject, body = data.subject, data.body
    if data.sample:
        # "Send me a sample": render against the newest job so the owner sees a real message.
        from sqlalchemy.orm import selectinload

        from backend.routers.documents import _resolve
        from backend.services.message_vars import render_for_order
        order = (await db.execute(select(Order).where(Order.org_id == current.org_id)
                                  .order_by(Order.created_at.desc())
                                  .options(selectinload(Order.client), selectinload(Order.lines),
                                           selectinload(Order.device), selectinload(Order.assigned_to)))).scalars().first()
        if order:
            variables = await _resolve(db, current.org_id, order)
            if isinstance(variables, tuple):
                variables = variables[0]
            # A preview never mints a customer's portal link (issue_links=False).
            subject, body = await render_for_order(db, current.org_id, order, subject, body or "",
                                                   doc_vars=variables, issue_links=False)
        if data.channel in ("sms", "whatsapp"):
            body = f"[Sample] {body}"
        elif subject:
            subject = f"[Sample] {subject}"
    _creds = await notif_creds(db, current.org_id, data.channel)
    res = await _adapter_for(data.channel, _creds).send(
        recipient=recipient, subject=subject, body=body)
    return {"status": res.get("status"), "recipient": recipient, "error": res.get("error"), "body": body}


@router.get("/rules/stats")
async def rule_stats(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Per customer rule: how many went out in the last 7 days, failures, and when the last
    one was sent. Plus how many customers can be texted (have a phone and haven't opted out)."""
    from datetime import timedelta

    from sqlalchemy import func

    from backend.models import Client, NotificationLog
    since = now_utc() - timedelta(days=7)
    rows = (await db.execute(select(
        NotificationLog.rule_id, NotificationLog.status, func.count(), func.max(NotificationLog.created_at))
        .where(NotificationLog.org_id == current.org_id, NotificationLog.rule_id.isnot(None),
               NotificationLog.created_at >= since)
        .group_by(NotificationLog.rule_id, NotificationLog.status))).all()
    out: dict = {}
    for rid, status, n, last in rows:
        d = out.setdefault(rid, {"sent_7d": 0, "failed_7d": 0, "skipped_7d": 0, "last_sent_at": None})
        if status == "sent":
            d["sent_7d"] += n
            d["last_sent_at"] = max(filter(None, [d["last_sent_at"], last.isoformat() if last else None]), default=None)
        elif status == "failed":
            d["failed_7d"] += n
        elif status == "skipped":
            d["skipped_7d"] += n
    with_phone = (await db.execute(select(func.count()).select_from(Client).where(
        Client.org_id == current.org_id, Client.phone.isnot(None), Client.phone != ""))).scalar() or 0
    opted_out = (await db.execute(select(func.count()).select_from(Client).where(
        Client.org_id == current.org_id, Client.phone.isnot(None), Client.phone != "",
        Client.consent_phone == False))).scalar() or 0  # noqa: E712
    return {"rules": out, "texting": {"with_phone": with_phone, "opted_out": opted_out}}


class QuietHoursIn(BaseModel):
    on: bool


@router.get("/quiet-hours")
async def get_quiet_hours(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Shop-wide view of the per-rule 'only during opening hours' setting for customer messages
    (reminders keep their own timing, as in PUT)."""
    rows = (await db.execute(select(NotificationRule).where(
        NotificationRule.org_id == current.org_id, NotificationRule.scope == "client",
        NotificationRule.event != "reminder"))).scalars().all()
    on = sum(1 for r in rows if r.working_hours_only)
    return {"on": bool(rows) and on == len(rows), "some": 0 < on < len(rows)}


@router.put("/quiet-hours")
async def set_quiet_hours(
    data: QuietHoursIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    """Hold every customer message that falls outside opening hours until the shop opens
    (reminders keep their own timing)."""
    rows = (await db.execute(select(NotificationRule).where(
        NotificationRule.org_id == current.org_id, NotificationRule.scope == "client",
        NotificationRule.event != "reminder"))).scalars().all()
    for r in rows:
        r.working_hours_only = data.on
    await db.commit()
    return {"ok": True, "changed": len(rows)}


@router.get("/variables")
async def variable_library(
    module: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """The template variable library, grouped like Orderry's. Reuses the document
    resolver's catalog so every variable shown here actually renders when a rule
    fires — plus a variable for every custom field the org has defined.

    `module` scopes the groups the way Orderry's per-module tabs do (N-P3): the
    Inquiries tab shows Lead vars, Workflows shows Order vars, and so on. Omit it
    for the full catalog.

    Table/row-repeat blocks from the document catalog are excluded: they only make
    sense inside a printed document, not an SMS or email body.
    """
    from backend.models import CustomField
    from backend.routers.documents import VARIABLE_GROUPS

    # Friendlier section names; keep the resolvable variable strings verbatim.
    RENAME = {"Ticket": "Order", "Device": "Asset"}
    # Short message variables first: the everyday ones, resolved by services/message_vars.
    from backend.services.message_vars import CLIENT_TOKENS
    short = [t for t, _ in SHORT_VARS]
    groups = {"Message": short if module in (None, "order") else
              [t for t in short if t in CLIENT_TOKENS]}
    for name, items in VARIABLE_GROUPS:
        if name == "Tables":            # not meaningful in a notification body
            continue
        groups[RENAME.get(name, name)] = list(items)

    fields = (await db.execute(select(CustomField).where(
        CustomField.org_id == current.org_id))).scalars().all()
    if fields:
        groups["Custom fields"] = [f"{{{f.field_label}}}" for f in fields]

    # N-P3: keep only the groups this module can actually resolve.
    allow = MODULE_VAR_GROUPS.get(module) if module else None
    if allow is not None:
        groups = {k: v for k, v in groups.items() if k in allow}

    # Preserve group order for the picker (dicts keep insertion order in py3.7+).
    order = ["Message", "Company", "Legal entity", "Client", "Payer", "Order", "Asset",
             "Employees", "Finance", "Date", "Links", "Other", "Custom fields"]
    ordered = {k: groups[k] for k in order if k in groups}
    for k in groups:                    # any group not in the explicit order
        ordered.setdefault(k, groups[k])
    return {"groups": ordered}
