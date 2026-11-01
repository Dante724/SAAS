"""Signed-in devices: one AuthSession row per sign-in, and the shop's per-login device limit.

Every token carries a `sid`. The row behind it is what "Signed-in devices" lists, what signing a
device out revokes, and what the device limit counts. At the limit nobody is evicted silently: after
the password (and 2FA) check the person gets a short-lived "session_limit" token that can only pick
one of their devices to sign out and finish signing in — so a thief holding the password can't lock
the owner out, and every eviction is visible and audited.
"""
import re
import secrets
import time
from datetime import datetime, timedelta
from typing import Optional

import jwt
from fastapi import HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import AuthSession, Employee, Organisation
from backend.services.audit import record as _audit
from backend.services.auth import create_access_token
from backend.services.ratelimit import client_ip

IDLE_DAYS = 14            # a device unused this long doesn't count toward the limit
TOUCH_EVERY = timedelta(minutes=5)
LIMIT_TOKEN_MIN = 5
LIMIT_CHOICES = (2, 3, 5, 10)


def device_label(ua: Optional[str], client: Optional[str] = None) -> str:
    """'Chrome on Mac' from a user agent. Coarse on purpose: enough to recognise a device."""
    ua = ua or ""
    if client == "go" or "TeklyGo" in ua:
        app = "Tekly Go"
    elif "Edg/" in ua:
        app = "Edge"
    elif "OPR/" in ua or "Opera" in ua:
        app = "Opera"
    elif "Firefox/" in ua or "FxiOS" in ua:
        app = "Firefox"
    elif "Chrome/" in ua or "CriOS" in ua:
        app = "Chrome"
    elif "Safari/" in ua:
        app = "Safari"
    else:
        app = "Browser"
    if "iPad" in ua:
        os_ = "iPad"
    elif "iPhone" in ua:
        os_ = "iPhone"
    elif "Android" in ua:
        os_ = "Android"
    elif re.search(r"Mac OS X|Macintosh", ua):
        os_ = "Mac"
    elif "Windows" in ua:
        os_ = "Windows"
    elif "CrOS" in ua:
        os_ = "Chromebook"
    elif "Linux" in ua:
        os_ = "Linux"
    else:
        os_ = "unknown device"
    return f"{app} on {os_}"


def _active_filter(emp: Employee, now: datetime):
    # A device is signed in while its current token is valid: a web session that was never
    # refreshed is over after its 24 h token, even though the sign-in could have slid for 30 days.
    return (AuthSession.employee_id == emp.id, AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > now, AuthSession.token_expires_at > now,
            AuthSession.token_version == int(emp.token_version or 0))


def token_expiry(auth_time: int, now: Optional[int] = None) -> datetime:
    """Mirrors create_access_token: min(now + token lifetime, end of the sign-in)."""
    now = int(time.time()) if now is None else now
    return datetime.utcfromtimestamp(min(now + settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
                                         auth_time + settings.REFRESH_TOKEN_EXPIRE_MINUTES * 60))


async def active_sessions(db: AsyncSession, emp: Employee) -> list[AuthSession]:
    now = datetime.utcnow()
    return list((await db.execute(select(AuthSession).where(*_active_filter(emp, now))
                                  .order_by(AuthSession.last_seen_at.desc()))).scalars())


async def device_limit(db: AsyncSession, emp: Employee) -> Optional[int]:
    org = (await db.execute(select(Organisation).where(Organisation.id == emp.org_id))).scalar_one_or_none()
    n = getattr(org, "max_sessions_per_login", None) if org else None
    return int(n) if n else None


async def counted_sessions(db: AsyncSession, emp: Employee) -> list[AuthSession]:
    """Sessions that count toward the limit: live, and used in the last IDLE_DAYS."""
    cutoff = datetime.utcnow() - timedelta(days=IDLE_DAYS)
    return [s for s in await active_sessions(db, emp) if s.last_seen_at and s.last_seen_at > cutoff]


async def issue_session(db: AsyncSession, emp: Employee, request: Optional[Request], via: str = "password",
                        mfa: bool = False) -> str:
    """Create the device row and mint its first token. The only place a sign-in is born."""
    now = int(time.time())
    sid = secrets.token_urlsafe(12)
    ua = request.headers.get("user-agent") if request is not None else None
    client = request.headers.get("x-tekly-client") if request is not None else None
    row = AuthSession(
        sid=sid, employee_id=emp.id, org_id=emp.org_id, token_version=int(emp.token_version or 0),
        created_at=datetime.utcfromtimestamp(now), last_seen_at=datetime.utcfromtimestamp(now),
        expires_at=datetime.utcfromtimestamp(now + settings.REFRESH_TOKEN_EXPIRE_MINUTES * 60),
        token_expires_at=token_expiry(now, now),
        ip=(client_ip(request) if request is not None else None), device=device_label(ua, client)[:80],
        via=via, mfa=mfa)
    db.add(row)
    await db.flush()
    await _audit(db, org_id=emp.org_id, actor_id=emp.id, action="auth.session_created",
                 entity_type="employee", entity_id=emp.id,
                 diff={"session": row.id, "device": row.device, "ip": row.ip, "via": via})
    await db.commit()
    return create_access_token({"sub": emp.id, "org_id": emp.org_id, "tv": int(emp.token_version or 0),
                                "sid": sid, "auth_time": now})


async def revoke(db: AsyncSession, row: AuthSession, *, actor_id: Optional[str], reason: str) -> None:
    if row.revoked_at is not None:
        return
    row.revoked_at = datetime.utcnow()
    row.revoked_by = actor_id
    row.revoke_reason = reason
    await _audit(db, org_id=row.org_id, actor_id=actor_id, action="auth.session_revoked",
                 entity_type="employee", entity_id=row.employee_id,
                 diff={"session": row.id, "device": row.device, "reason": reason})


async def revoke_all(db: AsyncSession, emp: Employee, *, actor_id: Optional[str], reason: str,
                     keep_sid: Optional[str] = None) -> int:
    n = 0
    for row in await active_sessions(db, emp):
        if keep_sid and row.sid == keep_sid:
            continue
        await revoke(db, row, actor_id=actor_id, reason=reason)
        n += 1
    return n


async def touch(db: AsyncSession, sid: str, last_seen: Optional[datetime]) -> None:
    """'Last active', written at most once per TOUCH_EVERY per device. The caller has already read
    last_seen_at, so most requests write nothing; the UPDATE stays conditional for races."""
    now = datetime.utcnow()
    if last_seen is not None and last_seen >= now - TOUCH_EVERY:
        return
    await db.execute(update(AuthSession).where(AuthSession.sid == sid, AuthSession.last_seen_at < now - TOUCH_EVERY)
                     .values(last_seen_at=now))
    await db.commit()


def serialize(row: AuthSession, current_sid: Optional[str] = None, *, show_ip: bool = True) -> dict:
    return {
        "id": row.id, "device": row.device or "Unknown device", "ip": row.ip if show_ip else None,
        "signed_in_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "last_active_at": row.last_seen_at.isoformat() + "Z" if row.last_seen_at else None,
        "this_device": bool(current_sid) and row.sid == current_sid,
    }


# ── At the limit ────────────────────────────────────────────────────────────────
def limit_token(emp: Employee, mfa: bool) -> str:
    """Good for LIMIT_TOKEN_MIN minutes and for one thing only: POST /api/auth/session-limit/continue.
    get_current_employee refuses any token with a `typ`, so it can't be used as a session."""
    now = int(time.time())
    return jwt.encode({"sub": emp.id, "typ": "session_limit", "tv": int(emp.token_version or 0), "mfa": mfa,
                       "iat": now, "exp": now + LIMIT_TOKEN_MIN * 60}, settings.SECRET_KEY,
                      algorithm=settings.ALGORITHM)


def read_limit_token(raw: str) -> dict:
    try:
        claims = jwt.decode(raw, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(401, "That took too long. Please sign in again.")
    if claims.get("typ") != "session_limit":
        raise HTTPException(401, "Please sign in again.")
    return claims


async def limit_reached(db: AsyncSession, emp: Employee, mfa: bool) -> None:
    """Raise the at-limit response (409) if this login already uses all its devices."""
    limit = await device_limit(db, emp)
    if not limit:
        return
    # Two sign-ins at once must not both squeeze under the limit: hold the employee row until the
    # caller's issue_session commits (FOR UPDATE on Postgres; SQLite serialises writes anyway).
    await db.execute(select(Employee.id).where(Employee.id == emp.id).with_for_update())
    live = await counted_sessions(db, emp)
    if len(live) < limit:
        return
    await _audit(db, org_id=emp.org_id, actor_id=emp.id, action="auth.session_limit_hit",
                 entity_type="employee", entity_id=emp.id, diff={"limit": limit, "active": len(live)})
    await db.commit()
    raise HTTPException(status_code=409, headers={"X-Session-Limit": "1"}, detail={
        "message": f"You're signed in on {len(live)} devices, the most your shop allows. "
                   f"Sign out of one to continue here.",
        "limit": limit,
        "sessions": [serialize(s, show_ip=False) for s in live],
        "limit_token": limit_token(emp, mfa),
    })
