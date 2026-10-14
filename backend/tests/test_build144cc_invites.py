"""Build 144cc — teammate invite → accept flow."""
import pytest
from sqlalchemy import select

from backend.db.database import AsyncSessionLocal
from backend.models import Employee, Invite


async def _owner(client):
    r = await client.post("/api/auth/register", json={
        "org_name": "InviteCo", "name": "Owner", "email": "owner@inv.dev", "password": "owner-pass-123"})
    assert r.status_code == 201
    tok = r.json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


async def _raw_token_for(email):
    """Reach into the DB to recover the raw invite token the mailer would send."""
    import hashlib
    async with AsyncSessionLocal() as db:
        inv = (await db.execute(select(Invite).where(Invite.email == email))).scalar_one()
    # we can't reverse the hash; instead re-issue by matching in the accept test via a known token
    return inv, hashlib


@pytest.mark.asyncio
async def test_invite_create_list_and_accept(client):
    hdr = await _owner(client)
    # create invite
    r = await client.post("/api/staff/invites", json={"email": "tech@inv.dev", "name": "Tara Tech"}, headers=hdr)
    assert r.status_code == 201, r.text
    # it appears in the pending list
    lst = await client.get("/api/staff/invites", headers=hdr)
    assert any(i["email"] == "tech@inv.dev" for i in lst.json()["items"])
    # recover the raw token the way the email would carry it: re-create to get a known token
    # (the endpoint refreshes the token on an existing open invite)
    import hashlib
    import secrets
    raw = secrets.token_urlsafe(32)
    async with AsyncSessionLocal() as db:
        inv = (await db.execute(select(Invite).where(Invite.email == "tech@inv.dev"))).scalar_one()
        inv.token_hash = hashlib.sha256(raw.encode()).hexdigest()
        await db.commit()
    # preview works
    pv = await client.post("/api/auth/invite/preview", json={"token": raw})
    assert pv.status_code == 200 and pv.json()["valid"] is True
    assert pv.json()["email"] == "tech@inv.dev"
    # accept → creates the employee and returns a session
    acc = await client.post("/api/auth/invite/accept",
                            json={"token": raw, "name": "Tara Tech", "password": "tara-pass-123"})
    assert acc.status_code == 200, acc.text
    assert acc.json()["access_token"]
    # the new teammate can log in
    login = await client.post("/api/auth/token",
                              data={"username": "tech@inv.dev", "password": "tara-pass-123"})
    assert login.status_code == 200
    # and is verified + gone from the pending list
    async with AsyncSessionLocal() as db:
        emp = (await db.execute(select(Employee).where(Employee.email == "tech@inv.dev"))).scalar_one()
        assert emp.email_verified is True
    lst2 = await client.get("/api/staff/invites", headers=hdr)
    assert not any(i["email"] == "tech@inv.dev" for i in lst2.json()["items"])


@pytest.mark.asyncio
async def test_invite_duplicate_email_blocked(client):
    hdr = await _owner(client)
    # inviting the owner's own email should be blocked (already on the team)
    r = await client.post("/api/staff/invites", json={"email": "owner@inv.dev"}, headers=hdr)
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_invite_accept_rejects_bad_token(client):
    r = await client.post("/api/auth/invite/accept",
                          json={"token": "not-a-real-token", "name": "X", "password": "long-enough-1"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_invite_accept_short_password(client):
    hdr = await _owner(client)
    await client.post("/api/staff/invites", json={"email": "short2@inv.dev"}, headers=hdr)
    import hashlib
    import secrets
    raw = secrets.token_urlsafe(32)
    async with AsyncSessionLocal() as db:
        inv = (await db.execute(select(Invite).where(Invite.email == "short2@inv.dev"))).scalar_one()
        inv.token_hash = hashlib.sha256(raw.encode()).hexdigest()
        await db.commit()
    r = await client.post("/api/auth/invite/accept", json={"token": raw, "name": "S", "password": "short"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_invite_requires_auth(client):
    assert (await client.get("/api/staff/invites")).status_code == 401
