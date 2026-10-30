"""Audit 29 Oct 2026, M6: the platform super-admin had no 2-step sign-in, its tokens couldn't be
revoked, and an unknown email answered faster than a wrong password (which emails exist)."""
import time

import pytest
from sqlalchemy import select

from backend.config import settings
from backend.db.database import AsyncSessionLocal
from backend.models import PlatformAdmin
from backend.services import platform_auth
from backend.services.auth import hash_password
from backend.services.mfa import totp

EMAIL, PW = "m6ops@test.dev", "portal-pass-123"


async def _seed():
    async with AsyncSessionLocal() as db:
        if not (await db.execute(select(PlatformAdmin).where(PlatformAdmin.email == EMAIL))).scalar_one_or_none():
            db.add(PlatformAdmin(email=EMAIL, name="Ops", password_hash=hash_password(PW)))
            await db.commit()


async def _admin():
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(PlatformAdmin).where(PlatformAdmin.email == EMAIL))).scalar_one()


async def _login(client, **extra):
    return await client.post("/api/admin/login", json={"email": EMAIL, "password": PW, **extra})


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


async def _enroll(client):
    """Turn 2-step on; returns (secret, fresh token)."""
    tok = (await _login(client)).json()["access_token"]
    setup = await client.post("/api/admin/mfa/setup", headers=_h(tok))
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret"]
    assert setup.json()["otpauth_uri"].startswith("otpauth://")
    on = await client.post("/api/admin/mfa/enable", json={"code": totp(secret)}, headers=_h(tok))
    assert on.status_code == 200, on.text
    return secret, on.json()["access_token"], tok


@pytest.mark.asyncio
async def test_two_step_is_required_once_turned_on(client):
    await _seed()
    secret, _new, _old = await _enroll(client)

    no_code = await _login(client)
    assert no_code.status_code == 401 and no_code.headers.get("X-MFA-Required") == "1"
    assert (await _login(client, code="000000")).status_code == 401
    # a code from a minute later than the one used to enable it
    ok = await _login(client, code=totp(secret, at=int(time.time()) + 30))
    assert ok.status_code == 200, ok.text
    assert (await client.get("/api/admin/me", headers=_h(ok.json()["access_token"]))).status_code == 200


@pytest.mark.asyncio
async def test_a_code_works_only_once(client):
    await _seed()
    secret, _new, _old = await _enroll(client)
    code = totp(secret, at=int(time.time()) + 30)
    assert (await _login(client, code=code)).status_code == 200
    assert (await _login(client, code=code)).status_code == 401


@pytest.mark.asyncio
async def test_secret_is_encrypted_at_rest(client, monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    await _seed()
    secret, _new, _old = await _enroll(client)
    row = await _admin()
    assert row.mfa_secret and secret not in row.mfa_secret and row.mfa_secret.startswith("enc:v1:")


@pytest.mark.asyncio
async def test_enabling_two_step_signs_out_older_tokens(client):
    await _seed()
    _secret, new, old = await _enroll(client)
    assert (await client.get("/api/admin/me", headers=_h(old))).status_code == 401
    assert (await client.get("/api/admin/me", headers=_h(new))).status_code == 200


@pytest.mark.asyncio
async def test_sign_out_everywhere_revokes_tokens(client):
    await _seed()
    tok = (await _login(client)).json()["access_token"]
    assert (await client.post("/api/admin/logout", headers=_h(tok))).status_code == 204
    assert (await client.get("/api/admin/me", headers=_h(tok))).status_code == 401


@pytest.mark.asyncio
async def test_old_tokens_without_a_version_are_refused(client):
    await _seed()
    from datetime import datetime, timedelta

    import jwt
    admin = await _admin()
    legacy = jwt.encode({"sub": admin.id, "scope": "platform", "exp": datetime.utcnow() + timedelta(hours=1)},
                        settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    assert (await client.get("/api/admin/me", headers=_h(legacy))).status_code == 401


@pytest.mark.asyncio
async def test_deployed_portal_needs_two_step_before_tenant_data(client, monkeypatch):
    await _seed()
    tok = (await _login(client)).json()["access_token"]
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    blocked = await client.get("/api/admin/tenants", headers=_h(tok))
    assert blocked.status_code == 403 and "2-step" in blocked.json()["detail"]
    me = await client.get("/api/admin/me", headers=_h(tok))
    assert me.status_code == 200 and me.json()["mfa_enabled"] is False
    assert (await client.post("/api/admin/mfa/setup", headers=_h(tok))).status_code == 200


@pytest.mark.asyncio
async def test_unknown_email_pays_the_password_check(client, monkeypatch):
    calls = []
    real = platform_auth.verify_password

    def spy(pw, h):
        calls.append(h)
        return real(pw, h)

    monkeypatch.setattr(platform_auth, "verify_password", spy)
    r = await client.post("/api/admin/login", json={"email": "nobody@test.dev", "password": "whatever-123"})
    assert r.status_code == 401
    assert len(calls) == 1          # a bcrypt check ran even though no account exists


@pytest.mark.asyncio
async def test_turning_two_step_off_needs_password_and_code(client):
    await _seed()
    secret, tok, _old = await _enroll(client)
    later = int(time.time()) + 30
    bad = await client.post("/api/admin/mfa/disable", json={"password": "wrong-pass", "code": totp(secret, at=later)},
                            headers=_h(tok))
    assert bad.status_code == 403
    off = await client.post("/api/admin/mfa/disable", json={"password": PW, "code": totp(secret, at=later)},
                            headers=_h(tok))
    assert off.status_code == 200, off.text
    assert (await _login(client)).status_code == 200
