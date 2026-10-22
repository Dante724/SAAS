"""B1 — MFA enrollment/management endpoints (login gate lives in auth.login)."""
import asyncio
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import Employee
from backend.services.audit import record as audit_record
from backend.services.auth import get_current_employee, verify_password
from backend.services.ratelimit import aenforce
from backend.services.mfa import (
    make_recovery_codes,
    new_secret,
    provisioning_uri,
    verify,
)

router = APIRouter(prefix="/api/mfa", tags=["mfa"])


@router.get("/status")
async def mfa_status(current: Employee = Depends(get_current_employee)):
    return {"enabled": current.mfa_enabled}


@router.post("/enroll")
async def mfa_enroll(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Begin enrollment: generate a secret + otpauth URI for the authenticator
    app. Not active until /activate confirms a code."""
    if current.mfa_enabled:
        raise HTTPException(400, "MFA already enabled")
    secret = new_secret()
    current.mfa_secret = secret
    await db.commit()
    return {"secret": secret,
            "otpauth_uri": provisioning_uri(secret, account=current.email)}


class ActivateIn(BaseModel):
    code: str


@router.post("/activate")
async def mfa_activate(
    data: ActivateIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Confirm a code from the authenticator, flip MFA on, and return one-time
    recovery codes (shown once)."""
    await aenforce("mfa_activate", current.id, 10, 900)   # a 6-digit code can't be guessed in bulk
    if not current.mfa_secret:
        raise HTTPException(400, "Start enrollment first")
    if not verify(current.mfa_secret, data.code):
        raise HTTPException(400, "Invalid code")
    plain, hashed = await asyncio.to_thread(make_recovery_codes)
    current.mfa_enabled = True
    current.mfa_recovery_codes = hashed
    await audit_record(db, org_id=current.org_id, actor_id=current.id, action="mfa.enable",
                       entity_type="employee", entity_id=current.id)
    await db.commit()
    return {"enabled": True, "recovery_codes": plain}


class DisableIn(BaseModel):
    password: str


@router.post("/disable")
async def mfa_disable(
    data: DisableIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Turn MFA off (requires the account password to prevent hijack). Blocked
    when the organisation mandates 2FA (build 144cd)."""
    from sqlalchemy import select

    from backend.models import Organisation
    org = (await db.execute(select(Organisation).where(
        Organisation.id == current.org_id))).scalar_one_or_none()
    if org and org.require_mfa:
        raise HTTPException(400, "Your organisation requires two-factor authentication for everyone")
    if not await asyncio.to_thread(verify_password, data.password, current.password_hash):
        raise HTTPException(403, "Password incorrect")
    current.mfa_enabled = False
    current.mfa_secret = None
    current.mfa_recovery_codes = None
    await audit_record(db, org_id=current.org_id, actor_id=current.id, action="mfa.disable",
                       entity_type="employee", entity_id=current.id)
    await db.commit()
    return {"enabled": False}
