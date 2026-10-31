from datetime import timedelta
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
import jwt
from jwt import PyJWTError as JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.db.database import get_db
from backend.models import Employee

# The password every staff account created from the React screen used to get (H1), and the demo
# seed's old passwords that the README published (launch QA F10). They're public knowledge, so they
# can never be set again and never open a session.
OLD_DEFAULT_PASSWORDS = frozenset({"changeme123", "admin123", "tech123"})


def is_old_default_password(password: str | None) -> bool:
    return (password or "") in OLD_DEFAULT_PASSWORDS

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")


# Build 144ai: hash with the `bcrypt` library directly rather than through passlib.
# passlib 1.7.4 (its last release) is incompatible with bcrypt 5.x — its backend
# self-test raises "password cannot be longer than 72 bytes" on import, which broke
# startup on fresh Python 3.13 installs. Calling bcrypt directly works on every
# bcrypt release and stays compatible with existing $2b$-format hashes.
def _pw_bytes(password: str) -> bytes:
    # bcrypt only considers the first 72 bytes; truncate to match (and to avoid the
    # ValueError bcrypt 5.x raises for longer inputs). This mirrors passlib's behaviour.
    return password.encode("utf-8")[:72]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_pw_bytes(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(_pw_bytes(plain), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False

def norm_email(email: Optional[str]) -> str:
    """Emails are matched case-insensitively and stored lowercase: A@x.com and a@x.com used to
    be two different accounts."""
    return (email or "").strip().lower()


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """`auth_time` is when the user actually signed in. /refresh carries it forward, so a
    session can slide for at most REFRESH_TOKEN_EXPIRE_MINUTES — before, refreshing every day
    kept one sign-in (or one stolen token) alive forever."""
    import time
    import secrets as _secrets
    to_encode = data.copy()
    now = int(time.time())        # epoch seconds — JWT NumericDate, timezone-free
    auth_time = int(to_encode.setdefault("auth_time", now))
    # One id per sign-in, carried through /refresh like auth_time: logout revokes exactly this
    # session (revoked_sessions) without signing the user out on their other devices.
    to_encode.setdefault("sid", _secrets.token_urlsafe(12))
    ttl = int((expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)).total_seconds())
    session_end = auth_time + settings.REFRESH_TOKEN_EXPIRE_MINUTES * 60
    to_encode.update({"exp": min(now + ttl, session_end)})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def session_expired(auth_time: Optional[int]) -> bool:
    import time
    if auth_time is None:
        return False
    return time.time() >= int(auth_time) + settings.REFRESH_TOKEN_EXPIRE_MINUTES * 60

# While a user owes 2FA enrollment (their org requires it), only these API paths answer:
# enough to load who they are, enroll, refresh and sign out.
MFA_ENROLLMENT_PATHS = ("/api/mfa/", "/api/auth/me", "/api/auth/refresh", "/api/auth/logout")


def assert_org_active(org, today=None) -> None:
    """Suspended or lapsed orgs are locked out of every credential they hold — staff tokens
    and public-API keys alike."""
    from datetime import date as _date
    if org is None:
        return
    today = today or _date.today()
    if org.sub_status == "suspended":
        # The admin panel can leave the shop a message ("Payment overdue, call us on ...").
        msg = (getattr(org, "suspend_message", None) or "").strip()
        raise HTTPException(status_code=403, detail=msg or "This account is suspended. Please contact support.")
    if getattr(org, "sub_end", None) and org.sub_end < today:
        raise HTTPException(status_code=403, detail="Your subscription has ended. Please contact support to renew.")


async def get_current_employee(
    request: Request,
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> Employee:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        employee_id: str = payload.get("sub")
        if employee_id is None:
            raise credentials_exception
        # Special-purpose tokens (e.g. the device-limit "pick a device to sign out" token) are
        # never a session.
        if payload.get("typ"):
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    # The role rides along in the same query so the cost-redaction decision is made here, once,
    # instead of by the middleware opening a second session after the endpoint.
    from backend.models import Role
    row = (await db.execute(select(Employee, Role).outerjoin(Role, Role.id == Employee.role_id)
                            .where(Employee.id == employee_id))).first()
    employee, role = (row[0], row[1]) if row else (None, None)
    if employee is None or not employee.active:
        raise credentials_exception
    # The flush-time tenant guard (db/tenancy.py) refuses writes to another shop's rows.
    db.sync_session.info["tenant_org"] = employee.org_id
    # Remembered for response-level rules (cost redaction in main.py) without re-reading the token.
    request.state.employee_id = employee.id
    request.state.employee_role_id = employee.role_id
    if employee.role_id:
        from backend.services.permissions import role_allows
        from backend.services.redact import COST_PERM
        request.state.can_see_costs = role is not None and role_allows(role, COST_PERM)
    else:
        # No role: the shop owner sees everything, a roleless invitee nothing.
        request.state.can_see_costs = await is_shop_owner(db, employee)
    # Lost-device logout: a token carrying a stale version is rejected. Tokens
    # minted before this feature have no "tv" claim and stay valid until expiry.
    tv = payload.get("tv")
    if tv is not None and int(tv) != int(employee.token_version or 0):
        raise credentials_exception
    # Signed out on this device (POST /logout): the token stays mathematically valid until it
    # expires, so the session id is checked against the revocation list.
    sid = payload.get("sid")
    if sid:
        from backend.models import AuthSession, RevokedSession
        dev = (await db.execute(select(AuthSession.revoked_at, AuthSession.last_seen_at)
                                .where(AuthSession.sid == sid))).first()
        if dev is not None:
            # Signed out from "Signed-in devices" (by them, an admin, or to make room at the limit).
            if dev[0] is not None:
                raise credentials_exception
            from backend.services.sessions import touch
            await touch(db, sid, dev[1])
        elif (await db.execute(select(RevokedSession.sid).where(RevokedSession.sid == sid))).first():
            # Tokens from before devices were tracked: still honour a sign-out.
            raise credentials_exception
    # Build 144do (security): enforce org lifecycle + employee access window on
    # EVERY request, not just at login. Previously a suspended/expired org's
    # existing tokens stayed valid for up to the 24h token lifetime ("instant
    # suspension" wasn't instant). Fail-closed: block suspended/expired orgs and
    # out-of-window employees. Platform admins use a separate identity and are
    # unaffected.
    from datetime import date as _date
    from backend.models import Organisation
    today = _date.today()
    org = (await db.execute(select(Organisation).where(
        Organisation.id == employee.org_id))).scalar_one_or_none()
    assert_org_active(org, today)
    if (employee.access_start and employee.access_start > today) or \
       (employee.access_end and employee.access_end < today):
        raise HTTPException(status_code=403, detail="Your access is outside its allowed dates.")
    # Org-mandated 2FA is enforced here, on every request — not just flagged at login for the
    # client to (maybe) act on. Checked per request so tokens issued earlier are covered too.
    if org is not None and getattr(org, "require_mfa", False) and not employee.mfa_enabled:
        if not request.url.path.startswith(MFA_ENROLLMENT_PATHS):
            raise HTTPException(status_code=403, detail="Set up two-factor authentication to continue.",
                                headers={"X-MFA-Enrollment-Required": "1"})
    # Who is acting on this request's session: backend/db/actors.py stamps it on discounts,
    # refunds and drawer closes, whichever route makes them.
    db.info["actor_id"] = employee.id
    return employee


async def is_shop_owner(db: AsyncSession, employee: Employee) -> bool:
    """The shop's creator (organisations.owner_id). Their sign-up role is an explicit
    allow-list, so without this a permission added later would lock the owner out of it."""
    from backend.models import Organisation
    owner_id = (await db.execute(select(Organisation.owner_id).where(
        Organisation.id == employee.org_id))).scalar_one_or_none()
    return bool(owner_id) and owner_id == employee.id


async def assert_not_self(db: AsyncSession, current: Employee, employee_id: str, what: str) -> None:
    """Separation of duties for pay: nobody approves, rewards or pays themself — except the
    owner, who answers to no one in their own shop."""
    if employee_id == current.id and current.role_id and not await is_shop_owner(db, current):
        raise HTTPException(403, f"You can't {what}. Ask the owner or another manager.")


async def has_perm(db: AsyncSession, employee: Employee, perm_key: str) -> bool:
    """Does this person's role grant the permission? (services/permissions.role_allows.)
    The shop owner has every permission. Anyone else with no role has none: an invitee
    accepted without a role used to be treated as the owner."""
    if getattr(employee, "api_key_id", None):
        return True           # an API v2 key (orders.ApiActor): its key scope is its permission
    if await is_shop_owner(db, employee):
        return True
    if not employee.role_id:
        return False
    from backend.models import Role
    from backend.services.permissions import role_allows
    role = (await db.execute(select(Role).where(Role.id == employee.role_id))).scalar_one_or_none()
    if role is None:
        return False          # a dangling role id never fails open
    return role_allows(role, perm_key)


async def assert_perm(db: AsyncSession, employee: Employee, perm_key: str) -> None:
    """In-handler variant of require_perm, for permissions that depend on the request
    (e.g. which report is being run)."""
    if not await has_perm(db, employee, perm_key):
        raise HTTPException(status_code=403, detail=f"Your role does not allow this action ({perm_key})")


def require_perm(perm_key: str):
    """Dependency factory for role-based gating (see has_perm). The shop owner is always
    allowed."""
    async def _checker(
        db: AsyncSession = Depends(get_db),
        current: Employee = Depends(get_current_employee),
    ) -> Employee:
        await assert_perm(db, current, perm_key)
        return current
    return _checker


async def check_location_access(db, current, location_id):
    """Item 2: enforce role location scope. A location-scoped role may only act
    on its allowed locations. No role, all-scope, or no location => allowed.
    Additive: raises 403 only when a scoped role hits an out-of-scope location."""
    if not location_id or not getattr(current, "role_id", None):
        return
    import json as _json

    from backend.models import Role
    role = (await db.execute(select(Role).where(Role.id == current.role_id))).scalar_one_or_none()
    if not role or getattr(role, "location_scope", "all") != "selected":
        return
    try:
        allowed = _json.loads(role.location_ids or "[]")
    except Exception:
        allowed = []
    if location_id not in allowed:
        raise HTTPException(status_code=403,
                            detail="Your role is not allowed to act on this location")


async def record_scope(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
) -> None:
    """Router dependency enforcing role record scope on {order_id}/{client_id} routes."""
    from backend.services.scoping import guard_record_scope
    await guard_record_scope(request, db, current)
