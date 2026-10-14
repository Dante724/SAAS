"""Build 144cb — password reset + email verification flows.

Uses the token service directly to obtain the raw token (in production it's
delivered by email; the SimulatedAdapter just logs it), then drives the public
endpoints exactly as the frontend would.
"""
import pytest
from sqlalchemy import select

from backend.db.database import AsyncSessionLocal
from backend.models import Employee
from backend.services import tokens as tok


async def _register(client, email="pw@acme.dev", pw="orig-pass-123"):
    r = await client.post("/api/auth/register", json={
        "org_name": "PWCo", "name": "Owner", "email": email, "password": pw})
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.asyncio
async def test_forgot_is_silent_for_unknown_email(client):
    r = await client.post("/api/auth/password/forgot", json={"email": "nobody@nowhere.dev"})
    assert r.status_code == 200
    assert "reset link" in r.json()["message"].lower()


@pytest.mark.asyncio
async def test_reset_password_end_to_end(client):
    await _register(client, email="reset@acme.dev", pw="orig-pass-123")
    # old password works
    assert (await client.post("/api/auth/token",
            data={"username": "reset@acme.dev", "password": "orig-pass-123"})).status_code == 200
    # issue a reset token as the mailer would
    async with AsyncSessionLocal() as db:
        emp = (await db.execute(select(Employee).where(Employee.email == "reset@acme.dev"))).scalar_one()
        raw = await tok.issue(db, employee_id=emp.id, kind="reset")
        await db.commit()
    # complete the reset
    done = await client.post("/api/auth/password/reset", json={"token": raw, "new_password": "brand-new-9999"})
    assert done.status_code == 200
    # new password works, old one does not
    assert (await client.post("/api/auth/token",
            data={"username": "reset@acme.dev", "password": "brand-new-9999"})).status_code == 200
    assert (await client.post("/api/auth/token",
            data={"username": "reset@acme.dev", "password": "orig-pass-123"})).status_code == 401


@pytest.mark.asyncio
async def test_reset_token_is_single_use(client):
    await _register(client, email="once@acme.dev", pw="orig-pass-123")
    async with AsyncSessionLocal() as db:
        emp = (await db.execute(select(Employee).where(Employee.email == "once@acme.dev"))).scalar_one()
        raw = await tok.issue(db, employee_id=emp.id, kind="reset")
        await db.commit()
    assert (await client.post("/api/auth/password/reset",
            json={"token": raw, "new_password": "first-pass-123"})).status_code == 200
    # reuse rejected
    assert (await client.post("/api/auth/password/reset",
            json={"token": raw, "new_password": "second-pass-123"})).status_code == 400


@pytest.mark.asyncio
async def test_reset_rejects_short_password(client):
    await _register(client, email="short@acme.dev")
    async with AsyncSessionLocal() as db:
        emp = (await db.execute(select(Employee).where(Employee.email == "short@acme.dev"))).scalar_one()
        raw = await tok.issue(db, employee_id=emp.id, kind="reset")
        await db.commit()
    assert (await client.post("/api/auth/password/reset",
            json={"token": raw, "new_password": "short"})).status_code == 400


@pytest.mark.asyncio
async def test_email_verification_flow(client):
    await _register(client, email="verify@acme.dev")
    async with AsyncSessionLocal() as db:
        emp = (await db.execute(select(Employee).where(Employee.email == "verify@acme.dev"))).scalar_one()
        assert emp.email_verified is False
        raw = await tok.issue(db, employee_id=emp.id, kind="verify")
        await db.commit()
    assert (await client.post("/api/auth/email/verify", json={"token": raw})).status_code == 200
    async with AsyncSessionLocal() as db:
        emp = (await db.execute(select(Employee).where(Employee.email == "verify@acme.dev"))).scalar_one()
        assert emp.email_verified is True


@pytest.mark.asyncio
async def test_wrong_kind_token_rejected(client):
    await _register(client, email="kind@acme.dev")
    async with AsyncSessionLocal() as db:
        emp = (await db.execute(select(Employee).where(Employee.email == "kind@acme.dev"))).scalar_one()
        raw = await tok.issue(db, employee_id=emp.id, kind="verify")
        await db.commit()
    # a verify token must not work as a reset token
    assert (await client.post("/api/auth/password/reset",
            json={"token": raw, "new_password": "long-enough-123"})).status_code == 400
