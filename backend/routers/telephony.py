"""B7 — Live telephony: provider config, click-to-call, and an incoming-call
feed. Provider-agnostic: the 'simulated' provider needs no external creds and
logs a Call so the whole flow is usable and testable out of the box; real
providers (twilio/aircall/zadarma) plug in behind the same interface. Inbound
calls still arrive via the existing token-gated /api/comms/calls/webhook."""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import Call, Client, Employee, TelephonyConfig
from backend.services.auth import get_current_employee, require_perm

router = APIRouter(prefix="/api/telephony", tags=["telephony"])


def _ser_cfg(c: Optional[TelephonyConfig]) -> dict:
    if not c:
        return {"provider": "simulated", "enabled": False, "caller_id_number": None, "config": {}}
    return {"provider": c.provider, "enabled": c.enabled,
            "caller_id_number": c.caller_id_number, "config": c.config or {}}


@router.get("/config")
async def get_config(db: AsyncSession = Depends(get_db),
                     current: Employee = Depends(get_current_employee)):
    c = (await db.execute(select(TelephonyConfig).where(
        TelephonyConfig.org_id == current.org_id))).scalar_one_or_none()
    return _ser_cfg(c)


class ConfigIn(BaseModel):
    provider: str = "simulated"
    enabled: bool = False
    caller_id_number: Optional[str] = None
    config: Optional[dict] = None


@router.put("/config")
async def set_config(data: ConfigIn, db: AsyncSession = Depends(get_db),
                     current: Employee = Depends(require_perm("can_manage_settings"))):
    if data.provider not in ("simulated", "twilio", "aircall", "zadarma"):
        raise HTTPException(422, "Unknown provider")
    c = (await db.execute(select(TelephonyConfig).where(
        TelephonyConfig.org_id == current.org_id))).scalar_one_or_none()
    if not c:
        c = TelephonyConfig(org_id=current.org_id)
        db.add(c)
    c.provider = data.provider
    c.enabled = data.enabled
    c.caller_id_number = data.caller_id_number
    c.config = data.config or {}
    await db.commit()
    return _ser_cfg(c)


class CallOut(BaseModel):
    to_number: str
    client_id: Optional[str] = None


@router.post("/call", status_code=201)
async def click_to_call(data: CallOut, db: AsyncSession = Depends(get_db),
                        current: Employee = Depends(get_current_employee)):
    """Place an outbound call. On the simulated provider this just logs the Call
    (a real provider would dial via its API here). Matches a client by number
    when one isn't supplied."""
    if not (data.to_number or "").strip():
        raise HTTPException(400, "A number is required")
    cfg = (await db.execute(select(TelephonyConfig).where(
        TelephonyConfig.org_id == current.org_id))).scalar_one_or_none()
    if not (cfg and cfg.enabled):
        raise HTTPException(409, "Telephony is not enabled for this company")
    provider = cfg.provider

    client_id = data.client_id
    if not client_id:
        digits = "".join(ch for ch in data.to_number if ch.isdigit())[-7:]
        if digits:
            match = (await db.execute(select(Client).where(
                Client.org_id == current.org_id, Client.phone.like(f"%{digits}")))).scalars().first()
            client_id = match.id if match else None

    call = Call(org_id=current.org_id, provider=provider, direction="outbound",
                from_number=(cfg.caller_id_number if cfg else None),
                to_number=data.to_number, client_id=client_id,
                employee_id=current.id,
                status="completed" if provider == "simulated" else "ringing")
    db.add(call)
    await db.commit()
    await db.refresh(call)
    return {"id": call.id, "provider": provider, "status": call.status,
            "client_id": client_id, "matched": client_id is not None}


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
