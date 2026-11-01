"""Admin panel sprint 1: locked-down sign-in.

- 2-step login is mandatory: a password alone reaches nothing; first login enrols.
- Codes can't be replayed; recovery codes work once.
- Sessions: own signing key + audience, 60 minutes, server-side logout, and
  deactivating an admin or bumping their token version ends every session.
- Same answer for an unknown email and a wrong password.
- Every /api/admin route needs a full session (walked automatically).
"""
import time

import jwt
import pytest
from sqlalchemy import select, update

from backend.config import settings
from backend.db.database import AsyncSessionLocal
from backend.models import PlatformAdmin
from backend.services import mfa
from backend.tests.admin_helpers import _SECRETS, PW, code_for, login, seed_admin


async def _ticket(client, email, pw=PW):
    r = await client.post("/api/admin/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_first_login_must_enrol_two_step_and_password_alone_reaches_nothing(client):
    email = await seed_admin()
    body = await _ticket(client, email)
    assert body["next"] == "mfa_setup" and "access_token" not in body
    hdr = {"Authorization": f"Bearer {body['ticket']}"}
    assert (await client.get("/api/admin/me", headers=hdr)).status_code == 401
    s = (await client.post("/api/admin/mfa/setup", json={"ticket": body["ticket"]})).json()
    assert s["secret"] and s["uri"].startswith("otpauth://")
    bad = await client.post("/api/admin/mfa/setup/confirm", json={"ticket": body["ticket"], "code": "000000"})
    assert bad.status_code == 401
    ok = await client.post("/api/admin/mfa/setup/confirm", json={
        "ticket": body["ticket"], "code": mfa.totp(s["secret"])})
    assert ok.status_code == 200, ok.text
    assert len(ok.json()["recovery_codes"]) == 8
    me = await client.get("/api/admin/me", headers={"Authorization": f"Bearer {ok.json()['access_token']}"})
    assert me.status_code == 200 and me.json()["email"] == email


@pytest.mark.asyncio
async def test_enrolled_admin_needs_a_fresh_code_and_codes_cannot_be_replayed(client):
    email = await seed_admin()
    await login(client, email)                       # enrols
    body = await _ticket(client, email)
    assert body["next"] == "mfa"
    wrong = await client.post("/api/admin/login/mfa", json={"ticket": body["ticket"], "code": "123456"})
    assert wrong.status_code == 401
    code = code_for(email, offset=30)
    ok = await client.post("/api/admin/login/mfa", json={"ticket": body["ticket"], "code": code})
    assert ok.status_code == 200, ok.text
    again = await _ticket(client, email)
    replay = await client.post("/api/admin/login/mfa", json={"ticket": again["ticket"], "code": code})
    assert replay.status_code == 401                 # the same code twice is refused


@pytest.mark.asyncio
async def test_recovery_code_works_once(client):
    email = await seed_admin()
    body = await _ticket(client, email)
    s = (await client.post("/api/admin/mfa/setup", json={"ticket": body["ticket"]})).json()
    _SECRETS[email] = s["secret"]
    codes = (await client.post("/api/admin/mfa/setup/confirm", json={
        "ticket": body["ticket"], "code": mfa.totp(s["secret"])})).json()["recovery_codes"]
    t1 = await _ticket(client, email)
    r1 = await client.post("/api/admin/login/mfa", json={"ticket": t1["ticket"], "recovery_code": codes[0]})
    assert r1.status_code == 200, r1.text
    t2 = await _ticket(client, email)
    r2 = await client.post("/api/admin/login/mfa", json={"ticket": t2["ticket"], "recovery_code": codes[0]})
    assert r2.status_code == 401


@pytest.mark.asyncio
async def test_unknown_email_and_wrong_password_look_the_same(client):
    email = await seed_admin()
    a = await client.post("/api/admin/login", json={"email": email, "password": "wrong-password-xx"})
    b = await client.post("/api/admin/login", json={"email": "nobody@nowhere.dev", "password": "wrong-password-xx"})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


@pytest.mark.asyncio
async def test_logout_ends_the_session(client):
    hdr = await login(client, await seed_admin())
    assert (await client.get("/api/admin/me", headers=hdr)).status_code == 200
    assert (await client.post("/api/admin/logout", headers=hdr)).status_code == 200
    assert (await client.get("/api/admin/me", headers=hdr)).status_code == 401


@pytest.mark.asyncio
async def test_deactivating_or_bumping_version_ends_sessions(client):
    email = await seed_admin()
    hdr = await login(client, email)
    async with AsyncSessionLocal() as db:
        await db.execute(update(PlatformAdmin).where(PlatformAdmin.email == email)
                         .values(token_version=PlatformAdmin.token_version + 1))
        await db.commit()
    assert (await client.get("/api/admin/me", headers=hdr)).status_code == 401
    hdr2 = await login(client, email)
    async with AsyncSessionLocal() as db:
        await db.execute(update(PlatformAdmin).where(PlatformAdmin.email == email).values(active=False))
        await db.commit()
    assert (await client.get("/api/admin/me", headers=hdr2)).status_code == 401


@pytest.mark.asyncio
async def test_old_style_platform_token_and_tenant_tokens_are_refused(client):
    email = await seed_admin()
    async with AsyncSessionLocal() as db:
        aid = (await db.execute(select(PlatformAdmin.id).where(PlatformAdmin.email == email))).scalar_one()
    # The pre-sprint token shape: signed with the shared key, scope claim only.
    old = jwt.encode({"sub": aid, "scope": "platform", "exp": int(time.time()) + 600},
                     settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    assert (await client.get("/api/admin/me", headers={"Authorization": f"Bearer {old}"})).status_code == 401
    r = await client.post("/api/auth/register", json={
        "org_name": "T", "name": "O", "email": f"t-{aid[:6]}@t.dev", "password": "pw-test-12345"})
    thdr = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert (await client.get("/api/admin/me", headers=thdr)).status_code == 401
    # and an admin token is not a shop token
    ahdr = await login(client, email)
    assert (await client.get("/api/auth/me", headers=ahdr)).status_code == 401


@pytest.mark.asyncio
async def test_session_token_lasts_an_hour_at_most(client):
    hdr = await login(client, await seed_admin())
    claims = jwt.decode(hdr["Authorization"].split()[1], options={"verify_signature": False})
    assert claims["aud"] == "tekly-admin" and claims.get("sid")
    assert claims["exp"] - claims["iat"] <= 3600


@pytest.mark.asyncio
async def test_every_admin_route_needs_a_full_session(client):
    """Walk the app's routes: each /api/admin route other than the sign-in steps must
    refuse no token and a password-only ticket."""
    from main import app
    email = await seed_admin()
    ticket = (await _ticket(client, email))["ticket"]
    public = {"/api/admin/login", "/api/admin/login/mfa", "/api/admin/mfa/setup", "/api/admin/mfa/setup/confirm"}
    seen = 0
    for path, ops in app.openapi()["paths"].items():
        if not path.startswith("/api/admin") or path in public:
            continue
        url = path.replace("{", "").replace("}", "")   # any value; auth runs first
        for method in sorted(m.upper() for m in ops):
            seen += 1
            for hdr in ({}, {"Authorization": f"Bearer {ticket}"}):
                r = await client.request(method, url, headers=hdr, json={})
                assert r.status_code == 401, f"{method} {path} answered {r.status_code} with {hdr or 'no token'}"
    assert seen >= 15


@pytest.mark.asyncio
async def test_weak_admin_passwords_are_refused(client):
    hdr = await login(client, await seed_admin())
    r = await client.post("/api/admin/admins", json={"email": "new@t.dev", "name": "N", "password": "short", "role": "support"},
                          headers=hdr)
    assert r.status_code == 422
