"""Build 144ce — authentication for the separate /admin platform portal.

Platform admins are operators of the whole SaaS (you), not members of any tenant.
Their tokens carry a distinct ``scope: "platform"`` claim so a tenant token can
never satisfy a platform endpoint and vice-versa.
"""
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
import jwt
from jwt import PyJWTError as JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.db.database import get_db
from backend.models import PlatformAdmin
from backend.services.auth import hash_password, verify_password

# Separate token URL from the tenant one; scope claim keeps the two worlds apart.
oauth2_platform = OAuth2PasswordBearer(tokenUrl="/api/admin/login", auto_error=True)

_SCOPE = "platform"


# Unknown emails are checked against this so they cost the same bcrypt time as a wrong password.
_DUMMY_HASH = hash_password("timing-equaliser-not-a-real-password")


def create_platform_token(admin: PlatformAdmin) -> str:
    expire = datetime.utcnow() + timedelta(hours=12)
    return jwt.encode({"sub": admin.id, "scope": _SCOPE, "tv": admin.token_version or 0, "exp": expire},
                      settings.SECRET_KEY, algorithm=settings.ALGORITHM)


async def authenticate(db: AsyncSession, email: str, password: str):
    """The admin for these credentials, or None. Runs one bcrypt check whether or not the email
    exists, so response time doesn't tell an attacker which addresses have accounts."""
    import asyncio
    admin = (await db.execute(select(PlatformAdmin).where(
        PlatformAdmin.email == (email or "").strip().lower()))).scalar_one_or_none()
    ok = await asyncio.to_thread(verify_password, password or "", admin.password_hash if admin else _DUMMY_HASH)
    return admin if (admin and ok and admin.active) else None


def _two_step_required() -> bool:
    """Deployed, the portal shows nothing but the admin's own account until 2-step is on."""
    return settings.is_deployed


async def _platform_admin(token: str, db: AsyncSession) -> PlatformAdmin:
    exc = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Platform authentication required",
                        headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        if payload.get("scope") != _SCOPE:
            raise exc
        admin_id = payload.get("sub")
        tv = payload.get("tv")
    except JWTError:
        raise exc
    admin = (await db.execute(select(PlatformAdmin).where(
        PlatformAdmin.id == admin_id))).scalar_one_or_none()
    if not admin or not admin.active:
        raise exc
    # Tokens from before token versions existed carry no "tv" and are refused too.
    if not isinstance(tv, int) or tv != (admin.token_version or 0):
        raise exc
    return admin


async def get_platform_admin_for_setup(
    token: str = Depends(oauth2_platform),
    db: AsyncSession = Depends(get_db),
) -> PlatformAdmin:
    """Signed-in admin, before 2-step is on: only for the account and 2-step setup routes."""
    return await _platform_admin(token, db)


async def get_current_platform_admin(
    token: str = Depends(oauth2_platform),
    db: AsyncSession = Depends(get_db),
) -> PlatformAdmin:
    admin = await _platform_admin(token, db)
    if _two_step_required() and not admin.mfa_enabled:
        raise HTTPException(403, "Turn on 2-step sign-in for this account before using the admin portal.")
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
            logging.getLogger("tekly").warning(
                "No platform admin seeded: set PLATFORM_ADMIN_EMAIL and PLATFORM_ADMIN_PASSWORD.")
            return
        email = email or "admin@platform.local"
        pw = pw or "change-me-please"   # dev-only convenience, never in production
    db.add(PlatformAdmin(email=email.lower(), name="Platform Admin",
                         password_hash=hash_password(pw)))
    await db.commit()
