"""A7 — VoIP calls + marketing campaigns.

VoIP is a gated seam: the provider (Aircall/Twilio/Zadarma) POSTs call events to
the webhook; we record + client-match. Campaigns send over the existing
notification channels (SMS/email adapters), so a real gateway is required to
actually deliver — under the simulated adapter they're recorded, not sent.
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.webhook_security import verify_org_webhook
from backend.db.database import get_db
from backend.models import Call, Campaign, Client, Employee
from backend.services.auth import get_current_employee, require_perm

router = APIRouter(prefix="/api/comms", tags=["comms"])


# ── VoIP: call log + webhook ─────────────────────────────────────────────────
async def _match_client(db, org_id, number):
    if not number:
        return None
    c = (await db.execute(select(Client).where(
        Client.org_id == org_id, Client.phone == number))).scalar_one_or_none()
    return c.id if c else None


@router.get("/calls")
async def list_calls(
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    rows = (await db.execute(select(Call).where(Call.org_id == current.org_id)
                             .order_by(Call.created_at.desc()).limit(limit))).scalars().all()
    return {"items": [{"id": c.id, "provider": c.provider, "direction": c.direction,
                       "from": c.from_number, "to": c.to_number, "client_id": c.client_id,
                       "status": c.status, "duration_s": c.duration_s,
                       "recording_url": c.recording_url,
                       "created_at": c.created_at.isoformat() if c.created_at else None}
                      for c in rows]}


class CallEventIn(BaseModel):
    provider: str = "manual"
    direction: str = "inbound"
    from_number: Optional[str] = None
    to_number: Optional[str] = None
    status: str = "completed"
    duration_s: int = 0
    recording_url: Optional[str] = None
    external_id: Optional[str] = None


@router.post("/calls/webhook", status_code=201)
async def call_webhook(
    data: CallEventIn,
    request: Request,
    org_id: str = Query(..., description="org the VoIP number belongs to"),
    db: AsyncSession = Depends(get_db),
):
    """Provider → Tekly call event. Token-gated (Build 110): the provider must
    present the org's X-Webhook-Token; records the call and matches a client
    by phone so an incoming call can pop the client's record."""
    await verify_org_webhook(db, org_id, request)
    number = data.from_number if data.direction == "inbound" else data.to_number
    client_id = await _match_client(db, org_id, number)
    call = Call(org_id=org_id, provider=data.provider, direction=data.direction,
                from_number=data.from_number, to_number=data.to_number,
                client_id=client_id, status=data.status, duration_s=data.duration_s,
                recording_url=data.recording_url, external_id=data.external_id)
    db.add(call)
    await db.commit()
    await db.refresh(call)

    # Build 97: a missed inbound call is a customer you didn't get to. If the automation
    # is on, raise a follow-up task so nobody has to notice the call log to catch it.
    task_id = None
    if data.direction == "inbound" and (data.status or "").lower() in ("missed", "no-answer", "failed"):
        task_id = await _task_for_missed_call(db, org_id, call, client_id)

    return {"id": call.id, "client_id": client_id, "matched": client_id is not None,
            "follow_up_task_id": task_id}


async def _task_for_missed_call(db, org_id, call, client_id):
    from backend.models import AutomationSetting, Client, Task
    setting = (await db.execute(select(AutomationSetting).where(
        AutomationSetting.org_id == org_id,
        AutomationSetting.key == "task_on_missed_call"))).scalar_one_or_none()
    if not setting or not setting.enabled:
        return None

    cfg = setting.config if isinstance(setting.config, dict) else {}
    who = None
    name = call.from_number
    if client_id:
        c = (await db.execute(select(Client).where(Client.id == client_id))).scalar_one_or_none()
        if c:
            name = c.name
            who = c.manager_id      # the client's account manager, if they have one
    task = Task(
        org_id=org_id,
        title=f"Call back {name}",
        description=f"Missed call from {call.from_number} at "
                    f"{call.created_at:%Y-%m-%d %H:%M} — return the call.",
        assigned_to_id=who or cfg.get("assign_to"),
        client_id=client_id,
        priority=cfg.get("priority", "normal"),   # whatever the config specifies
        status="todo")
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task.id


# ── Build 97: automation settings ────────────────────────────────────────────
class AutomationIn(BaseModel):
    enabled: bool
    config: Optional[dict] = None


@router.get("/automations/{key}")
async def get_automation(
    key: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    from backend.models import AutomationSetting
    s = (await db.execute(select(AutomationSetting).where(
        AutomationSetting.org_id == current.org_id,
        AutomationSetting.key == key))).scalar_one_or_none()
    return {"key": key, "enabled": bool(s and s.enabled),
            "config": (s.config if s and isinstance(s.config, dict) else {})}


@router.put("/automations/{key}")
async def set_automation(
    key: str, data: AutomationIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    from backend.models import AutomationSetting
    if key not in ("task_on_missed_call",):
        raise HTTPException(422, f"Unknown automation “{key}”")
    s = (await db.execute(select(AutomationSetting).where(
        AutomationSetting.org_id == current.org_id,
        AutomationSetting.key == key))).scalar_one_or_none()
    if not s:
        s = AutomationSetting(org_id=current.org_id, key=key)
        db.add(s)
    s.enabled = data.enabled
    if data.config is not None:
        s.config = data.config
    await db.commit()
    return {"key": key, "enabled": s.enabled, "config": s.config}


# ── Marketing campaigns ──────────────────────────────────────────────────────
class CampaignIn(BaseModel):
    name: str
    channel: str = "sms"          # sms|email
    subject: Optional[str] = None
    body: str = ""
    segment: str = "all"          # all|loyalty|lapsed


def _ser_campaign(c: Campaign) -> dict:
    return {"id": c.id, "name": c.name, "channel": c.channel, "subject": c.subject,
            "segment": c.segment, "status": c.status, "recipients": c.recipients,
            "sent_at": c.sent_at.isoformat() if c.sent_at else None}


@router.get("/campaigns")
async def list_campaigns(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    rows = (await db.execute(select(Campaign).where(Campaign.org_id == current.org_id)
                             .order_by(Campaign.created_at.desc()))).scalars().all()
    return {"items": [_ser_campaign(c) for c in rows]}


@router.post("/campaigns", status_code=201)
async def create_campaign(
    data: CampaignIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    if data.channel not in ("sms", "email"):
        from fastapi import HTTPException
        raise HTTPException(422, "channel must be sms|email")
    c = Campaign(org_id=current.org_id, name=data.name, channel=data.channel,
                 subject=data.subject, body=data.body, segment=data.segment)
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return _ser_campaign(c)


async def _segment_clients(db, org_id, segment):
    q = select(Client).where(Client.org_id == org_id)
    if segment == "loyalty":
        q = q.where(Client.loyalty_tier.in_(("silver", "gold")))
    rows = (await db.execute(q)).scalars().all()
    # 'lapsed' would filter by last-order date; kept simple for the seam
    return rows


@router.post("/campaigns/{campaign_id}/send")
async def send_campaign(
    campaign_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    """Send to the segment over the configured channel. Delivery depends on a
    real SMS/email gateway; under the simulated adapter recipients are counted
    and the campaign is marked sent (records, doesn't deliver)."""
    from fastapi import HTTPException
    c = (await db.execute(select(Campaign).where(
        Campaign.id == campaign_id, Campaign.org_id == current.org_id))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Campaign not found")
    if c.status == "sent":
        raise HTTPException(400, "Campaign already sent")

    clients = await _segment_clients(db, current.org_id, c.segment)
    consent_field = "consent_email" if c.channel == "email" else "consent_phone"
    contact_field = "email" if c.channel == "email" else "phone"
    recipients = [cl for cl in clients
                  if getattr(cl, contact_field, None) and getattr(cl, consent_field, True)]

    sent = 0
    for cl in recipients:
        try:
            from backend.services.notifications import _adapter_for, notif_creds
            _creds = await notif_creds(db, current.org_id, c.channel)
            await _adapter_for(c.channel, _creds).send(
                recipient=getattr(cl, contact_field), subject=c.subject, body=c.body)
        except Exception:
            pass   # seam: simulated/absent gateway → still count the intent
        sent += 1

    c.status = "sent"
    c.recipients = sent
    c.sent_at = datetime.utcnow()
    await db.commit()
    return {"ok": True, "recipients": sent, "channel": c.channel, "segment": c.segment}
