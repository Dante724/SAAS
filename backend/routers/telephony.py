"""B7 — Live telephony: provider config, click-to-call, and an incoming-call
feed. The 'simulated' provider needs no external creds and logs a Call so the
whole flow is usable and testable out of the box (and can simulate an inbound
call for demos). D1 adds a real Twilio Voice adapter: each shop connects its own
Twilio account (encrypted via org_creds, never returned), click-to-call goes
through Twilio's REST Calls API, and Twilio's signed webhooks land on
/api/comms/twilio/voice and /api/comms/twilio/status. D2's contacts-from-calls
automation is configured here too. Aircall/Zadarma are accepted as config
values for older setups but have no adapter yet."""
from datetime import datetime, timedelta
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import AutomationSetting, Call, Client, Employee, Tag, TelephonyConfig
from backend.services import call_contacts, org_creds, twilio_voice
from backend.services.auth import get_current_employee, require_perm

router = APIRouter(prefix="/api/telephony", tags=["telephony"])

VOICE = "twilio_voice"   # org_creds provider key for the shop's Twilio voice account


async def _cfg(db: AsyncSession, org_id: str) -> Optional[TelephonyConfig]:
    return (await db.execute(select(TelephonyConfig).where(
        TelephonyConfig.org_id == org_id))).scalar_one_or_none()


async def twilio_status(db: AsyncSession, org_id: str) -> dict:
    """What the settings card shows about the shop's Twilio account. No secrets."""
    creds = await org_creds.effective(db, org_id, VOICE)
    connected = bool(creds.get("account_sid") and creds.get("auth_token"))
    mode = creds.get("mode") if connected else None
    return {"connected": connected, "mode": mode,
            "test_mode": mode == "test",
            "live_allowed": twilio_voice.live_allowed(),
            "voice_url": twilio_voice.webhook_url("voice", org_id),
            "status_url": twilio_voice.webhook_url("status", org_id),
            "test_number": twilio_voice.TEST_FROM_NUMBER}


async def _ser_cfg(db: AsyncSession, org_id: str, c: Optional[TelephonyConfig]) -> dict:
    cfg = (c.config or {}) if c else {}
    return {"provider": c.provider if c else "simulated", "enabled": bool(c and c.enabled),
            "caller_id_number": c.caller_id_number if c else None,
            "forward_to": cfg.get("forward_to"),
            "twilio": await twilio_status(db, org_id)}


@router.get("/config")
async def get_config(db: AsyncSession = Depends(get_db),
                     current: Employee = Depends(get_current_employee)):
    return await _ser_cfg(db, current.org_id, await _cfg(db, current.org_id))


class ConfigIn(BaseModel):
    provider: str = "simulated"
    enabled: bool = False
    caller_id_number: Optional[str] = Field(None, max_length=40)
    forward_to: Optional[str] = Field(None, max_length=40)   # where inbound Twilio calls ring
    config: Optional[dict] = None                          # legacy; only forward_to is kept


@router.put("/config")
async def set_config(data: ConfigIn, db: AsyncSession = Depends(get_db),
                     current: Employee = Depends(require_perm("can_manage_settings"))):
    if data.provider not in ("simulated", "twilio", "aircall", "zadarma"):
        raise HTTPException(422, "Unknown provider")
    number = (data.caller_id_number or "").strip() or None
    forward = (data.forward_to or (data.config or {}).get("forward_to") or "").strip() or None
    if data.provider == "twilio" and data.enabled:
        tw = await twilio_status(db, current.org_id)
        if not tw["connected"]:
            raise HTTPException(409, "Connect your Twilio account before turning Twilio calls on")
        if not number:
            raise HTTPException(422, "Enter your Twilio phone number")
    for n in (number, forward):
        if n and len(call_contacts.digits(n)) < 7:
            raise HTTPException(422, f"“{n}” doesn't look like a phone number")
    c = await _cfg(db, current.org_id)
    if not c:
        c = TelephonyConfig(org_id=current.org_id)
        db.add(c)
    c.provider = data.provider
    c.enabled = data.enabled
    c.caller_id_number = number
    kept = {k: v for k, v in (c.config or {}).items() if k != "forward_to"}   # other providers' keys
    c.config = {**kept, **({"forward_to": forward} if forward else {})}
    await db.commit()
    return await _ser_cfg(db, current.org_id, c)


# ── D1: the shop's Twilio account ────────────────────────────────────────────
class TwilioCredsIn(BaseModel):
    account_sid: str = Field(..., max_length=64)
    auth_token: str = Field(..., max_length=128)


def _raise(e: twilio_voice.TwilioError):
    raise HTTPException(e.status, e.message)


def _guard_live(mode: str):
    if mode == "live" and not twilio_voice.live_allowed():
        raise HTTPException(403, "These are live Twilio credentials. Live calling is turned off "
                                 "on this server, so use your Twilio test credentials for now.")


@router.put("/twilio")
async def connect_twilio(data: TwilioCredsIn, db: AsyncSession = Depends(get_db),
                         current: Employee = Depends(require_perm("can_manage_settings"))):
    """Check the keys with Twilio, refuse live ones unless allowed, then store them
    encrypted. Nothing is stored when the check fails."""
    sid, token = data.account_sid.strip(), data.auth_token.strip()
    try:
        result = await twilio_voice.probe(sid, token)
    except twilio_voice.TwilioError as e:
        _raise(e)
    _guard_live(result["mode"])
    await org_creds.set_creds(db, current.org_id, VOICE,
                              {"account_sid": sid, "auth_token": token, "mode": result["mode"]})
    return {**(await twilio_status(db, current.org_id)), "account_name": result["account_name"]}


@router.post("/twilio/test")
async def test_twilio(db: AsyncSession = Depends(get_db),
                      current: Employee = Depends(require_perm("can_manage_settings"))):
    """Re-check the stored keys with Twilio (no call is placed with live keys)."""
    creds = await org_creds.effective(db, current.org_id, VOICE)
    if not (creds.get("account_sid") and creds.get("auth_token")):
        raise HTTPException(409, "Connect your Twilio account first")
    try:
        result = await twilio_voice.probe(creds["account_sid"], creds["auth_token"])
    except twilio_voice.TwilioError as e:
        _raise(e)
    if result["mode"] != creds.get("mode"):     # keys were swapped for another kind on Twilio's side
        await org_creds.set_creds(db, current.org_id, VOICE, {**creds, "mode": result["mode"]})
    _guard_live(result["mode"])
    return {"ok": True, "mode": result["mode"], "account_name": result["account_name"]}


@router.delete("/twilio")
async def disconnect_twilio(db: AsyncSession = Depends(get_db),
                            current: Employee = Depends(require_perm("can_manage_settings"))):
    await org_creds.delete_creds(db, current.org_id, VOICE)
    c = await _cfg(db, current.org_id)
    if c and c.provider == "twilio":
        c.enabled = False
        await db.commit()
    return await _ser_cfg(db, current.org_id, c)


# ── Click-to-call ────────────────────────────────────────────────────────────
class CallOut(BaseModel):
    to_number: str = Field(..., max_length=40)
    client_id: Optional[str] = None


@router.post("/call", status_code=201)
async def click_to_call(data: CallOut, db: AsyncSession = Depends(get_db),
                        current: Employee = Depends(get_current_employee)):
    """Place an outbound call. Simulated: logs a completed Call. Twilio: rings the
    employee's own phone first, then connects them to the customer (the voice webhook
    returns the <Dial>). Matches — or, per the automation, creates — the contact."""
    to = (data.to_number or "").strip()
    if not to:
        raise HTTPException(400, "A number is required")
    cfg = await _cfg(db, current.org_id)
    if not (cfg and cfg.enabled):
        raise HTTPException(409, "Telephony is not enabled for this company")
    provider = cfg.provider

    if data.client_id:
        cl = (await db.execute(select(Client).where(
            Client.id == data.client_id, Client.org_id == current.org_id))).scalar_one_or_none()
        if not cl:
            raise HTTPException(404, "Client not found")

    creds = {}
    if provider == "twilio":
        creds = await org_creds.effective(db, current.org_id, VOICE)
        if not (creds.get("account_sid") and creds.get("auth_token")):
            raise HTTPException(409, "Connect your Twilio account in Settings → Integrations")
        _guard_live(creds.get("mode") or "live")
        if not cfg.caller_id_number:
            raise HTTPException(409, "Add your Twilio phone number in Settings → Integrations")
        if not (current.phone or "").strip():
            raise HTTPException(409, "Add your phone number to your profile: Twilio rings you "
                                     "first, then connects the customer")

    call = Call(org_id=current.org_id, provider=provider, direction="outbound",
                from_number=cfg.caller_id_number, to_number=to, employee_id=current.id,
                status="completed" if provider == "simulated" else "ringing")
    db.add(call)
    await db.flush()
    created = False

    async def link():
        nonlocal created
        if data.client_id:
            call.client_id = data.client_id
        else:
            created = (await call_contacts.link_call(db, call))["created"]

    if provider != "twilio":
        await link()
    else:   # link (and maybe create the contact) only once Twilio has accepted the call
        await db.commit()   # the status callback may arrive before Twilio's response does
        try:
            res = await twilio_voice.create_call(
                creds["account_sid"], creds["auth_token"],
                to=current.phone.strip(), from_=cfg.caller_id_number,
                url=twilio_voice.webhook_url("voice", current.org_id, call.id),
                status_callback=twilio_voice.webhook_url("status", current.org_id, call.id))
        except twilio_voice.TwilioError as e:
            call.status = "failed"
            await db.commit()
            _raise(e)
        if call.provider_sid is None:
            call.provider_sid = res.get("sid")
        await link()
    await db.commit()
    await db.refresh(call)
    return {"id": call.id, "provider": provider, "status": call.status,
            "provider_sid": call.provider_sid, "client_id": call.client_id,
            "matched": call.client_id is not None and not created, "client_created": created,
            "test_mode": provider == "twilio" and creds.get("mode") == "test"}


# ── D2: contacts from calls ──────────────────────────────────────────────────
class ContactAutomationIn(BaseModel):
    enabled: bool
    direction: Literal["incoming", "all"] = "incoming"
    tag_ids: list[str] = Field(default_factory=list, max_length=20)
    assign_manager: bool = True


@router.get("/automation/contacts")
async def get_contact_automation(db: AsyncSession = Depends(get_db),
                                 current: Employee = Depends(get_current_employee)):
    return await call_contacts.settings_for(db, current.org_id)


@router.put("/automation/contacts")
async def set_contact_automation(data: ContactAutomationIn, db: AsyncSession = Depends(get_db),
                                 current: Employee = Depends(require_perm("can_manage_settings"))):
    ids = list(dict.fromkeys(data.tag_ids))
    if ids:
        found = set((await db.execute(select(Tag.id).where(
            Tag.org_id == current.org_id, Tag.id.in_(ids)))).scalars().all())
        if len(found) != len(ids):
            raise HTTPException(404, "Tag not found")
    s = (await db.execute(select(AutomationSetting).where(
        AutomationSetting.org_id == current.org_id,
        AutomationSetting.key == call_contacts.AUTOMATION_KEY))).scalar_one_or_none()
    if not s:
        s = AutomationSetting(org_id=current.org_id, key=call_contacts.AUTOMATION_KEY)
        db.add(s)
    s.enabled = data.enabled
    s.config = {"direction": data.direction, "tag_ids": ids, "assign_manager": data.assign_manager}
    await db.commit()
    return await call_contacts.settings_for(db, current.org_id)


# ── Simulated inbound call (demo / try-it) ───────────────────────────────────
class SimInboundIn(BaseModel):
    from_number: str = Field(..., max_length=40)
    extension: Optional[str] = Field(None, max_length=20)


@router.post("/simulate/inbound", status_code=201)
async def simulate_inbound(data: SimInboundIn, db: AsyncSession = Depends(get_db),
                           current: Employee = Depends(require_perm("can_manage_settings"))):
    """Pretend a customer is calling, so a shop on the simulated provider can try the
    incoming-call card and the contacts-from-calls automation. Never touches a provider."""
    cfg = await _cfg(db, current.org_id)
    if cfg and cfg.enabled and cfg.provider != "simulated":
        raise HTTPException(409, "Switch the provider to Simulated to place a test call")
    if len(call_contacts.digits(data.from_number)) < 4:
        raise HTTPException(422, "Enter the caller's phone number")
    from backend.routers.comms import record_inbound_call
    res = await record_inbound_call(
        db, current.org_id, provider="simulated", from_number=data.from_number.strip(),
        to_number=(cfg.caller_id_number if cfg else None), status="ringing",
        extension=(data.extension or "").strip() or None)
    return res


@router.get("/incoming")
async def incoming_calls(since_seconds: int = 120, mine_only: bool = False,
                         db: AsyncSession = Depends(get_db),
                         current: Employee = Depends(get_current_employee)):
    """Recent inbound calls still ringing — powers the incoming-call card. With
    mine_only, restricts to calls dialed to the current employee's extension."""
    since = datetime.utcnow() - timedelta(seconds=max(since_seconds, 1))
    q = select(Call).where(
        Call.org_id == current.org_id, Call.direction == "inbound",
        Call.status.in_(("ringing", "in_progress")),
        Call.created_at >= since).order_by(Call.created_at.desc())
    rows = (await db.execute(q)).scalars().all()
    if mine_only and current.extension:
        rows = [c for c in rows if (c.to_number or "").endswith(current.extension)]
    out = []
    for c in rows:
        name = None
        if c.client_id:
            cl = (await db.execute(select(Client).where(Client.id == c.client_id))).scalar_one_or_none()
            name = cl.name if cl else None
        out.append({"id": c.id, "from": c.from_number, "to": c.to_number,
                    "client_id": c.client_id, "client_name": name,
                    "created_at": c.created_at.isoformat() if c.created_at else None})
    return {"items": out, "total": len(out)}
