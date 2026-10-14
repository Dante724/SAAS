"""Build 144ce — authentication for the separate /admin platform portal.

Platform admins are operators of the whole SaaS (you), not members of any tenant.
Their tokens carry a distinct ``scope: "platform"`` claim so a tenant token can
never satisfy a platform endpoint and vice-versa.
"""
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.db.database import get_db
from backend.models import PlatformAdmin
from backend.services.auth import hash_password

# Separate token URL from the tenant one; scope claim keeps the two worlds apart.
oauth2_platform = OAuth2PasswordBearer(tokenUrl="/api/admin/login", auto_error=True)

_SCOPE = "platform"


def create_platform_token(admin_id: str) -> str:
    expire = datetime.utcnow() + timedelta(hours=12)
    return jwt.encode({"sub": admin_id, "scope": _SCOPE, "exp": expire},
                      settings.SECRET_KEY, algorithm=settings.ALGORITHM)


async def get_current_platform_admin(
    token: str = Depends(oauth2_platform),
    db: AsyncSession = Depends(get_db),
) -> PlatformAdmin:
    exc = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Platform authentication required",
                        headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        if payload.get("scope") != _SCOPE:
            raise exc
        admin_id = payload.get("sub")
    except JWTError:
        raise exc
    admin = (await db.execute(select(PlatformAdmin).where(
        PlatformAdmin.id == admin_id))).scalar_one_or_none()
    if not admin or not admin.active:
        raise exc
    return admin


async def ensure_seed_admin(db: AsyncSession) -> None:
    """Create the bootstrap platform admin from env if none exists yet, so the
    portal is reachable on a fresh install. Configure PLATFORM_ADMIN_EMAIL /
    PLATFORM_ADMIN_PASSWORD.

    Security: in production we REFUSE to seed a weak default — if the env vars are
    missing, no admin is created (the portal simply has no account until you set
    them). A known default super-admin credential in production is a critical risk,
    so we never fall back to one there. A dev default is used only outside production."""
    import logging

    existing = (await db.execute(select(PlatformAdmin).limit(1))).scalar_one_or_none()
    if existing:
        return
    email = getattr(settings, "PLATFORM_ADMIN_EMAIL", "") or ""
    pw = getattr(settings, "PLATFORM_ADMIN_PASSWORD", "") or ""
    if not email or not pw:
        if settings.is_deployed:
            logging.getLogger("repairos").warning(
                "No platform admin seeded: set PLATFORM_ADMIN_EMAIL and PLATFORM_ADMIN_PASSWORD.")
            return
        email = email or "admin@platform.local"
        pw = pw or "change-me-please"   # dev-only convenience, never in production
    db.add(PlatformAdmin(email=email.lower(), name="Platform Admin",
                         password_hash=hash_password(pw)))
    await db.commit()
