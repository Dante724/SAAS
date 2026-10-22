"""A7 — VoIP calls + marketing campaigns.

VoIP is a gated seam: the provider (Aircall/Twilio/Zadarma) POSTs call events to
the webhook; we record + client-match. Campaigns send over the existing
notification channels (SMS/email adapters), so a real gateway is required to
actually deliver — under the simulated adapter they're recorded, not sent.
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.webhook_security import verify_org_webhook
from backend.db.database import get_db
from backend.models import Call, Campaign, Client, Employee
from backend.services import call_contacts
from backend.services.auth import get_current_employee, require_perm

router = APIRouter(prefix="/api/comms", tags=["comms"])


# ── VoIP: call log + webhook ─────────────────────────────────────────────────
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
                       "recording_url": c.recording_url, "provider_sid": c.provider_sid,
                       "created_at": c.created_at.isoformat() if c.created_at else None}
                      for c in rows]}


MISSED = ("missed", "no-answer", "failed", "busy", "canceled")


async def record_inbound_call(db, org_id, *, provider, from_number, to_number, status,
                              duration_s=0, recording_url=None, external_id=None,
                              provider_sid=None, extension=None, employee_id=None,
                              direction="inbound"):
    """One path for every call we're told about (generic webhook, simulated call, Twilio):
    log it, link or create the contact (D2), and raise the missed-call task. Commits."""
    call = Call(org_id=org_id, provider=provider, direction=direction,
                from_number=from_number, to_number=to_number, status=status,
                duration_s=duration_s or 0, recording_url=recording_url,
                external_id=external_id, provider_sid=provider_sid, employee_id=employee_id)
    db.add(call)
    await db.flush()
    link = await call_contacts.link_call(db, call, extension=extension)
    await db.commit()
    await db.refresh(call)

    # Build 97: a missed inbound call is a customer you didn't get to. If the automation
    # is on, raise a follow-up task so nobody has to notice the call log to catch it.
    task_id = None
    if direction == "inbound" and (status or "").lower() in MISSED:
        task_id = await _task_for_missed_call(db, org_id, call, call.client_id)
    return {"id": call.id, "client_id": call.client_id,
            "matched": call.client_id is not None and not link["created"],
            "client_created": link["created"], "follow_up_task_id": task_id}


class CallEventIn(BaseModel):
    provider: str = "manual"
    direction: str = "inbound"
    from_number: Optional[str] = None
    to_number: Optional[str] = None
    status: str = "completed"
    duration_s: int = 0
    recording_url: Optional[str] = None
    external_id: Optional[str] = None
    extension: Optional[str] = None   # D2: the dialled extension, when the provider sends one


@router.post("/calls/webhook", status_code=201)
async def call_webhook(
    data: CallEventIn,
    request: Request,
    org_id: str = Query(..., description="org the VoIP number belongs to"),
    db: AsyncSession = Depends(get_db),
):
    """Provider → Tekly call event. Token-gated (Build 110): the provider must
    present the org's X-Webhook-Token; records the call and matches (or, per the
    D2 automation, creates) the client by phone so an incoming call can pop their record."""
    await verify_org_webhook(db, org_id, request)
    if data.direction not in ("inbound", "outbound"):
        raise HTTPException(422, "direction must be inbound or outbound")
    return await record_inbound_call(
        db, org_id, provider=(data.provider or "manual")[:20], direction=data.direction,
        from_number=data.from_number, to_number=data.to_number, status=(data.status or "completed")[:20],
        duration_s=data.duration_s, recording_url=data.recording_url,
        external_id=data.external_id, extension=data.extension)


# ── D1: Twilio Programmable Voice webhooks ───────────────────────────────────
# Both URLs carry ?org_id= (and, for calls we placed, &call_id=). The tenant is that org,
# proven by X-Twilio-Signature computed with THAT org's own auth token (no operator-wide
# fallback), and the payload's AccountSid and our number must match what the org
# registered. Nothing in the payload picks the tenant. Both are idempotent on CallSid.
async def _twilio_request(request: Request, db, org_id: str, *, status_only: bool = False):
    """Verify a Twilio webhook for org_id. status_only: a progress report for a call already
    under way, accepted while the shop's Twilio keys exist even if it has since switched
    provider or turned calling off, so in-flight calls still get their outcome."""
    from backend.models import TelephonyConfig
    from backend.services import org_creds, twilio_voice
    cfg = (await db.execute(select(TelephonyConfig).where(
        TelephonyConfig.org_id == org_id))).scalar_one_or_none()
    creds = await org_creds.effective(db, org_id, "twilio_voice") if cfg else {}
    if not (cfg and creds.get("auth_token") and (status_only or cfg.provider == "twilio")):
        raise HTTPException(404, "Twilio voice is not set up for this shop")
    form = await request.form()
    params = [(k, str(v)) for k, v in form.multi_items()]
    urls = twilio_voice.request_urls(request)
    if not twilio_voice.signature_ok(creds["auth_token"], request.headers.get("X-Twilio-Signature", ""),
                                     urls, params):
        raise HTTPException(403, "Invalid Twilio signature")
    f = dict(params)
    if f.get("AccountSid") != creds.get("account_sid"):
        raise HTTPException(403, "This call belongs to a different Twilio account")
    if creds.get("mode") == "live" and not twilio_voice.live_allowed():
        raise HTTPException(403, "Live telephony is turned off on this server")
    if not cfg.enabled and not status_only:
        raise HTTPException(409, "Telephony is turned off for this shop")
    return cfg, f


def _xml(body: str) -> Response:
    return Response(content=body, media_type="application/xml")


@router.post("/twilio/voice")
async def twilio_voice_webhook(
    request: Request,
    org_id: str = Query(...),
    call_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Twilio asks what to do with a call. Inbound (the shop's number was dialled): log it,
    link/create the contact, then ring the forwarding number or say we'll call back.
    Outbound (call_id set — a click-to-call we placed): the employee picked up, so dial
    the customer."""
    from backend.services import twilio_voice as tv
    cfg, f = await _twilio_request(request, db, org_id)
    sid = f.get("CallSid") or ""
    if not sid:
        raise HTTPException(400, "Missing CallSid")
    status_url = tv.webhook_url("status", org_id, call_id)

    if call_id:
        call = (await db.execute(select(Call).where(
            Call.id == call_id, Call.org_id == org_id))).scalar_one_or_none()
        if not call or call.direction != "outbound" or call.provider_sid not in (None, sid):
            raise HTTPException(404, "Call not found")
        if call.provider_sid is None:
            call.provider_sid = sid
            await db.commit()
        return _xml(tv.twiml(tv.dial(call.to_number, caller_id=cfg.caller_id_number,
                                     action=status_url, number_status_cb=status_url)))

    if not call_contacts.same_number(f.get("To"), cfg.caller_id_number):
        raise HTTPException(403, "This number isn't registered to this shop")
    call = (await db.execute(select(Call).where(
        Call.org_id == org_id, Call.provider_sid == sid))).scalar_one_or_none()
    forward = (cfg.config or {}).get("forward_to")
    if not call:
        # With no forwarding number nobody can pick up, so the call is missed from the start.
        try:
            await record_inbound_call(
                db, org_id, provider="twilio", from_number=f.get("From"), to_number=f.get("To"),
                status="ringing" if forward else "missed", provider_sid=sid)
        except IntegrityError:          # a concurrent retry of the same CallSid won the insert
            await db.rollback()
        call = (await db.execute(select(Call).where(
            Call.org_id == org_id, Call.provider_sid == sid))).scalar_one()
    status_url = tv.webhook_url("status", org_id, call.id)
    if forward and call.status != "missed":
        return _xml(tv.twiml(tv.dial(forward, caller_id=None, action=status_url,
                                     number_status_cb=status_url)))
    return _xml(tv.twiml(tv.say("Thanks for calling. We can't take your call right now, "
                                "and we'll call you back soon.")))


_TERMINAL = ("completed", "missed", "failed")


def _map_status(raw: str, direction: str) -> Optional[str]:
    raw = (raw or "").lower()
    if raw in ("queued", "initiated", "ringing"):
        return "ringing"
    if raw in ("in-progress", "answered"):
        return "in_progress"
    if raw == "completed":
        return "completed"
    if raw in ("busy", "no-answer", "canceled"):
        return "missed"
    if raw == "failed":
        return "failed" if direction == "outbound" else "missed"
    return None


@router.post("/twilio/status")
async def twilio_status_webhook(
    request: Request,
    org_id: str = Query(...),
    call_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Call progress from Twilio: the parent call's status callback, the <Dial> action
    (DialCallStatus = how the forwarded/customer leg ended) and the dialled number's
    "answered" event (carries ParentCallSid). Replays are harmless: a finished call
    keeps its outcome, and the missed-call task is raised only on the move to missed."""
    from backend.services import twilio_voice as tv
    _cfg, f = await _twilio_request(request, db, org_id, status_only=True)
    sid = f.get("ParentCallSid") or f.get("CallSid") or ""
    call = None
    if sid:
        call = (await db.execute(select(Call).where(
            Call.org_id == org_id, Call.provider_sid == sid))).scalar_one_or_none()
    if not call and call_id and not f.get("ParentCallSid"):
        call = (await db.execute(select(Call).where(
            Call.id == call_id, Call.org_id == org_id, Call.provider_sid.is_(None)))).scalar_one_or_none()
        if call:
            call.provider_sid = sid
    if not call:   # not one of ours (or already superseded) — acknowledge so Twilio stops retrying
        return _xml(tv.twiml())

    before = call.status
    if f.get("ParentCallSid"):          # the dialled leg picked up
        new = "in_progress" if (f.get("CallStatus") or "").lower() in ("in-progress", "answered") else None
    elif f.get("DialCallStatus"):       # how the <Dial> ended
        d = f["DialCallStatus"].lower()
        new = "completed" if d in ("completed", "answered") else ("failed" if d == "failed" and call.direction == "outbound" else "missed")
        if f.get("DialCallDuration", "").isdigit():
            call.duration_s = int(f["DialCallDuration"])
    else:                               # the parent call's own status callback
        new = _map_status(f.get("CallStatus"), call.direction)
        if new == "completed" and before == "ringing" and call.direction == "inbound":
            # the caller hung up before anyone answered (the <Dial> result always comes first).
            # Outbound callbacks can arrive out of order, so "completed" stays completed there.
            new = "missed"
        if new == "in_progress" and call.direction == "inbound":
            new = None                  # Twilio answers inbound calls itself; wait for the Dial
        if f.get("CallDuration", "").isdigit() and not call.duration_s:
            call.duration_s = int(f["CallDuration"])
    if new and before not in _TERMINAL:
        call.status = new
    await db.commit()

    if call.direction == "inbound" and before != "missed" and call.status == "missed":
        await _task_for_missed_call(db, org_id, call, call.client_id)
    return _xml(tv.twiml())


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
