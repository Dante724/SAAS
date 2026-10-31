"""Authentication for the /admin platform portal (Tekly's own back office).

Platform admins operate the whole SaaS and belong to no shop. Sprint 1 hardening:

- Own signing key (PLATFORM_SECRET_KEY, else derived from SECRET_KEY with a separate
  label) and an ``aud`` claim, so a shop token can never verify as an admin token.
- Sign-in is password, then a 2-step code. The password step only returns a
  5-minute *ticket* that opens the 2-step endpoints and nothing else. First sign-in
  enrols 2-step (TOTP + 8 single-use recovery codes).
- A session token lasts 60 minutes and names a server-side session row: logout,
  deactivation, and bumping the admin's token_version end it on the next request.
- Tokens come as a Bearer header, or as the HttpOnly ``tekly_admin`` cookie together
  with the ``X-Tekly-Admin`` header (a cross-site page can't send that header).
"""
import hashlib
import hmac
import time
from datetime import datetime

import jwt
from fastapi import Depends, HTTPException, Request, status
from jwt import PyJWTError as JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.db.database import get_db
from backend.models import AdminSession, PlatformAdmin
from backend.services.auth import hash_password

AUD = "tekly-admin"
SESSION_SECONDS = 60 * 60
TICKET_SECONDS = 5 * 60
COOKIE = "tekly_admin"
CSRF_HEADER = "x-tekly-admin"

_WEAK = {"password1234", "123456789012", "qwertyuiop12", "changemeplease", "change-me-please",
         "adminadmin12", "letmein12345", "welcome12345", "tekly1234567", "portal123456"}


def _key() -> str:
    if settings.PLATFORM_SECRET_KEY:
        return settings.PLATFORM_SECRET_KEY
    return hmac.new(settings.SECRET_KEY.encode(), b"tekly-admin-signing-v1", hashlib.sha256).hexdigest()


def password_problem(pw: str | None) -> str | None:
    """None when the password is acceptable for an admin, else the reason."""
    pw = pw or ""
    if len(pw) < 12:
        return "Use at least 12 characters"
    if pw.lower() in _WEAK or len(set(pw)) < 5:
        return "That password is too easy to guess"
    return None


def _encode(claims: dict) -> str:
    return jwt.encode(claims, _key(), algorithm="HS256")


def _decode(token: str) -> dict:
    return jwt.decode(token, _key(), algorithms=["HS256"], audience=AUD,
                      options={"require": ["exp", "iat", "aud", "sub"]})


def create_ticket(admin: PlatformAdmin, stage: str) -> str:
    """After the password: opens only the 2-step steps (stage = mfa | mfa_setup)."""
    now = int(time.time())
    return _encode({"sub": admin.id, "aud": AUD, "typ": "ticket", "stage": stage,
                    "ver": admin.token_version or 0, "iat": now, "exp": now + TICKET_SECONDS})


async def admin_from_ticket(db: AsyncSession, ticket: str, stage: str) -> PlatformAdmin:
    bad = HTTPException(401, "Your sign-in expired. Start again.")
    try:
        c = _decode(ticket or "")
    except JWTError:
        raise bad
    if c.get("typ") != "ticket" or c.get("stage") != stage:
        raise bad
    # Row lock (Postgres) so two parallel requests can't both spend the same code.
    admin = (await db.execute(select(PlatformAdmin).where(PlatformAdmin.id == c.get("sub"))
                              .with_for_update())).scalar_one_or_none()
    if not admin or not admin.active or (admin.token_version or 0) != c.get("ver"):
        raise bad
    # A setup ticket can't be replayed once 2-step is on (it would replace the secret).
    if stage == "mfa_setup" and admin.mfa_enrolled_at:
        raise bad
    return admin


async def start_session(db: AsyncSession, admin: PlatformAdmin, ip: str | None) -> str:
    sess = AdminSession(admin_id=admin.id, ip=(ip or "")[:64])
    db.add(sess)
    admin.last_login_at = datetime.utcnow()
    await db.flush()
    now = int(time.time())
    return _encode({"sub": admin.id, "aud": AUD, "typ": "session", "sid": sess.id,
                    "ver": admin.token_version or 0, "iat": now, "exp": now + SESSION_SECONDS})


def _token_from(request: Request) -> str | None:
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    cookie = request.cookies.get(COOKIE)
    if cookie and request.headers.get(CSRF_HEADER) == "1":
        return cookie
    return None


async def get_current_platform_admin(request: Request, db: AsyncSession = Depends(get_db)) -> PlatformAdmin:
    exc = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Platform authentication required",
                        headers={"WWW-Authenticate": "Bearer"})
    token = _token_from(request)
    if not token:
        raise exc
    try:
        c = _decode(token)
    except JWTError:
        raise exc
    if c.get("typ") != "session" or not c.get("sid"):
        raise exc
    sess = (await db.execute(select(AdminSession).where(
        AdminSession.id == c["sid"], AdminSession.admin_id == c.get("sub")))).scalar_one_or_none()
    if not sess or sess.revoked_at is not None:
        raise exc
    admin = (await db.execute(select(PlatformAdmin).where(PlatformAdmin.id == c.get("sub")))).scalar_one_or_none()
    if not admin or not admin.active or (admin.token_version or 0) != c.get("ver") or not admin.mfa_enrolled_at:
        raise exc
    request.state.admin_session_id = sess.id
    return admin


async def end_session(db: AsyncSession, session_id: str | None) -> None:
    if not session_id:
        return
    sess = (await db.execute(select(AdminSession).where(AdminSession.id == session_id))).scalar_one_or_none()
    if sess and sess.revoked_at is None:
        sess.revoked_at = datetime.utcnow()


async def ensure_seed_admin(db: AsyncSession) -> None:
    """Create the bootstrap platform admin from env if none exists yet, so the
    portal is reachable on a fresh install. Configure PLATFORM_ADMIN_EMAIL /
    PLATFORM_ADMIN_PASSWORD.

    Security: deployed environments never get a default admin, and refuse a weak
    PLATFORM_ADMIN_PASSWORD. A dev default is used only outside deployed environments.
    The seeded admin is an owner (no role = owner) and must enrol 2-step on first login."""
    import logging

    existing = (await db.execute(select(PlatformAdmin).limit(1))).scalar_one_or_none()
    if existing:
        return
    log = logging.getLogger("tekly")
    email = getattr(settings, "PLATFORM_ADMIN_EMAIL", "") or ""
    pw = getattr(settings, "PLATFORM_ADMIN_PASSWORD", "") or ""
    if not email or not pw:
        if settings.is_deployed:
            log.warning("No platform admin seeded: set PLATFORM_ADMIN_EMAIL and PLATFORM_ADMIN_PASSWORD.")
            return
        email = email or "admin@platform.local"
        pw = pw or "change-me-please"   # dev-only convenience, never in a deployed environment
    elif settings.is_deployed and password_problem(pw):
        log.warning("No platform admin seeded: PLATFORM_ADMIN_PASSWORD is too weak (%s).", password_problem(pw))
        return
    db.add(PlatformAdmin(email=email.lower(), name="Platform Admin", password_hash=hash_password(pw)))
    await db.commit()
