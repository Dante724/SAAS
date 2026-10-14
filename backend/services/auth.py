from datetime import datetime, timedelta
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.db.database import get_db
from backend.models import Employee

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

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

async def get_current_employee(
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
    except JWTError:
        raise credentials_exception
    result = await db.execute(select(Employee).where(Employee.id == employee_id))
    employee = result.scalar_one_or_none()
    if employee is None or not employee.active:
        raise credentials_exception
    # Lost-device logout: a token carrying a stale version is rejected. Tokens
    # minted before this feature have no "tv" claim and stay valid until expiry.
    tv = payload.get("tv")
    if tv is not None and int(tv) != int(employee.token_version or 0):
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
    if org is not None:
        if org.sub_status == "suspended":
            raise HTTPException(status_code=403, detail="This account is suspended. Please contact support.")
        if getattr(org, "sub_end", None) and org.sub_end < today:
            raise HTTPException(status_code=403, detail="Your subscription has ended. Please contact support to renew.")
    if (employee.access_start and employee.access_start > today) or \
       (employee.access_end and employee.access_end < today):
        raise HTTPException(status_code=403, detail="Your access is outside its allowed dates.")
    return employee


def require_perm(perm_key: str):
    """Dependency factory for role-based gating. Default-allow (matching the
    existing can_add_without_stock semantics): the action is blocked only when
    the employee's role explicitly sets the permission to False. Employees with
    no role (org owners) are always allowed."""
    async def _checker(
        db: AsyncSession = Depends(get_db),
        current: Employee = Depends(get_current_employee),
    ) -> Employee:
        if current.role_id:
            from backend.models import Role
            role = (await db.execute(select(Role).where(Role.id == current.role_id))).scalar_one_or_none()
            perms = (role.permissions or {}) if role else {}
            if perms.get(perm_key) is False:
                raise HTTPException(status_code=403,
                                    detail=f"Your role does not allow this action ({perm_key})")
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
