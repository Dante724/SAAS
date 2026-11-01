"""Signed-in devices: your own (account menu → Security), a teammate's (Team → person → Sign-in),
and the "sign out a device and continue" step when a login is at the shop's device limit."""
from typing import Optional

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.db.database import get_db
from backend.models import AuthSession, Employee, Role
from backend.services import sessions as _s
from backend.services.audit import record as _audit
from backend.services.auth import get_current_employee, require_perm
from backend.services.ratelimit import aenforce as _rate

router = APIRouter(tags=["devices"])


def _current_sid(request: Request) -> Optional[str]:
    raw = (request.headers.get("authorization") or "").split(" ", 1)[-1]
    try:
        return jwt.decode(raw, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]).get("sid")
    except jwt.PyJWTError:
        return None


async def _own_row(db: AsyncSession, emp: Employee, session_id: str) -> AuthSession:
    row = (await db.execute(select(AuthSession).where(AuthSession.id == session_id,
                                                      AuthSession.employee_id == emp.id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "That device isn't signed in.")
    return row


# ── Your own devices ────────────────────────────────────────────────────────────
@router.get("/api/auth/sessions")
async def my_sessions(request: Request, db: AsyncSession = Depends(get_db),
                      current: Employee = Depends(get_current_employee)):
    sid = _current_sid(request)
    rows = await _s.active_sessions(db, current)
    return {"items": [_s.serialize(r, sid) for r in rows], "limit": await _s.device_limit(db, current)}


@router.post("/api/auth/sessions/{session_id}/sign-out")
async def sign_out_my_device(session_id: str, request: Request, db: AsyncSession = Depends(get_db),
                             current: Employee = Depends(get_current_employee)):
    await _rate("device_signout", current.id, limit=30, window_sec=60)
    row = await _own_row(db, current, session_id)
    if row.sid == _current_sid(request):
        raise HTTPException(400, "Use Sign out to leave this device.")
    await _s.revoke(db, row, actor_id=current.id, reason="other")
    await db.commit()
    return {"ok": True}


@router.post("/api/auth/sessions/sign-out-others")
async def sign_out_other_devices(request: Request, db: AsyncSession = Depends(get_db),
                                 current: Employee = Depends(get_current_employee)):
    await _rate("device_signout", current.id, limit=30, window_sec=60)
    n = await _s.revoke_all(db, current, actor_id=current.id, reason="other", keep_sid=_current_sid(request))
    await db.commit()
    return {"ok": True, "signed_out": n}


# ── At the device limit ─────────────────────────────────────────────────────────
class LimitContinue(BaseModel):
    limit_token: str
    session_id: str


@router.post("/api/auth/session-limit/continue")
async def continue_at_limit(data: LimitContinue, request: Request, db: AsyncSession = Depends(get_db)):
    """The password and 2FA already passed (that's how the limit token was issued): sign out the
    chosen device and finish signing in here, without asking for the password again."""
    claims = _s.read_limit_token(data.limit_token)
    await _rate("device_limit", claims["sub"], limit=10, window_sec=300)
    emp = (await db.execute(select(Employee).where(Employee.id == claims["sub"]))).scalar_one_or_none()
    if emp is None or not emp.active or int(claims.get("tv", -1)) != int(emp.token_version or 0):
        raise HTTPException(401, "Please sign in again.")
    row = await _own_row(db, emp, data.session_id)
    await _s.revoke(db, row, actor_id=emp.id, reason="limit")
    await db.commit()
    await _s.limit_reached(db, emp, mfa=bool(claims.get("mfa")))    # still over (limit lowered): pick another
    await _audit(db, org_id=emp.org_id, actor_id=emp.id, action="auth.login",
                 entity_type="employee", entity_id=emp.id, diff={"ip": _s.client_ip(request), "after_limit": True})
    token = await _s.issue_session(db, emp, request, via="password", mfa=bool(claims.get("mfa")))
    role = (await db.execute(select(Role).where(Role.id == emp.role_id))).scalar_one_or_none()
    from backend.routers.auth import TokenResponse
    return TokenResponse(access_token=token, employee_id=emp.id, org_id=emp.org_id, name=emp.name,
                         role=role.name if role else "staff")


# ── A teammate's devices (admins) ───────────────────────────────────────────────
async def _teammate(db: AsyncSession, current: Employee, emp_id: str) -> Employee:
    emp = (await db.execute(select(Employee).where(Employee.id == emp_id, Employee.org_id == current.org_id))
           ).scalar_one_or_none()
    if emp is None:
        raise HTTPException(404, "Employee not found")
    from backend.routers.staff import _assert_can_manage
    await _assert_can_manage(db, current, emp)
    return emp


@router.get("/api/staff/employees/{emp_id}/sessions")
async def teammate_sessions(emp_id: str, db: AsyncSession = Depends(get_db),
                            current: Employee = Depends(require_perm("can_manage_employees"))):
    emp = await _teammate(db, current, emp_id)
    return {"items": [_s.serialize(r) for r in await _s.active_sessions(db, emp)]}


@router.post("/api/staff/employees/{emp_id}/sessions/{session_id}/sign-out")
async def sign_out_teammate_device(emp_id: str, session_id: str, db: AsyncSession = Depends(get_db),
                                   current: Employee = Depends(require_perm("can_manage_employees"))):
    emp = await _teammate(db, current, emp_id)
    await _rate("device_signout", current.id, limit=30, window_sec=60)
    await _s.revoke(db, await _own_row(db, emp, session_id), actor_id=current.id, reason="admin")
    await db.commit()
    return {"ok": True}


@router.post("/api/staff/employees/{emp_id}/sessions/sign-out-all")
async def sign_out_teammate_everywhere(emp_id: str, db: AsyncSession = Depends(get_db),
                                       current: Employee = Depends(require_perm("can_manage_employees"))):
    emp = await _teammate(db, current, emp_id)
    await _rate("device_signout", current.id, limit=30, window_sec=60)
    n = await _s.revoke_all(db, emp, actor_id=current.id, reason="admin")
    await db.commit()
    return {"ok": True, "signed_out": n}
