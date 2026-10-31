"""Shared helpers for platform-admin tests: seed an admin with a role and sign in
through the full 2-step flow (enrolment on first login)."""
import time
import uuid

from sqlalchemy import select

from backend.db.database import AsyncSessionLocal
from backend.models import AdminRole, PlatformAdmin
from backend.services import mfa
from backend.services.auth import hash_password

PW = "portal-pass-123456"


async def seed_admin(email: str | None = None, *, role: str | None = "owner", pw: str = PW, active: bool = True) -> str:
    """Create an admin with one of the built-in roles (None = legacy owner). Returns the email."""
    from backend.services.admin_access import ensure_builtin_roles
    email = (email or f"ops-{uuid.uuid4().hex[:8]}@test.dev").lower()
    async with AsyncSessionLocal() as db:
        await ensure_builtin_roles(db)
        role_id = None
        if role:
            role_id = (await db.execute(select(AdminRole.id).where(AdminRole.builtin == role))).scalar_one()
        if not (await db.execute(select(PlatformAdmin).where(PlatformAdmin.email == email))).scalar_one_or_none():
            db.add(PlatformAdmin(email=email, name=email.split("@")[0], password_hash=hash_password(pw),
                                 role_id=role_id, active=active))
        await db.commit()
    return email


_SECRETS: dict[str, str] = {}


def code_for(email: str, *, offset: int = 0) -> str:
    return mfa.totp(_SECRETS[email], at=int(time.time()) + offset)


async def login(client, email: str, pw: str = PW) -> dict:
    """Sign in, enrolling 2-step on first login. Returns Authorization headers."""
    r = await client.post("/api/admin/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    body = r.json()
    ticket = body["ticket"]
    if body["next"] == "mfa_setup":
        s = await client.post("/api/admin/mfa/setup", json={"ticket": ticket})
        assert s.status_code == 200, s.text
        _SECRETS[email] = s.json()["secret"]
        c = await client.post("/api/admin/mfa/setup/confirm", json={"ticket": ticket, "code": code_for(email)})
        assert c.status_code == 200, c.text
        tok = c.json()["access_token"]
    else:
        # A code can be used once; on a re-login inside the same 30 s use the next step.
        c = await client.post("/api/admin/login/mfa", json={"ticket": ticket, "code": code_for(email, offset=30)})
        assert c.status_code == 200, c.text
        tok = c.json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


async def admin_headers(client, role: str | None = "owner") -> tuple[dict, str]:
    email = await seed_admin(role=role)
    return await login(client, email), email
