"""Build 144cd — org-mandated 2FA: login flags enrollment, self-disable blocked,
admin can reset a teammate's 2FA."""
import pytest
from sqlalchemy import select

from backend.db.database import AsyncSessionLocal
from backend.models import Employee, Organisation


async def _owner(client, email="mfa-owner@acme.dev"):
    r = await client.post("/api/auth/register", json={
        "org_name": "MfaCo", "name": "Owner", "email": email, "password": "owner-pass-123"})
    assert r.status_code == 201
    return r.json(), {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _require(client, hdr):
    """Mandatory 2FA is opt-in (security phase 1): the owner switches it on."""
    r = await client.put("/api/staff/company", json={"require_mfa": True}, headers=hdr)
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_new_org_does_not_require_mfa_by_default(client):
    await _owner(client, "req-default@acme.dev")
    async with AsyncSessionLocal() as db:
        emp = (await db.execute(select(Employee).where(Employee.email == "req-default@acme.dev"))).scalar_one()
        org = (await db.execute(select(Organisation).where(Organisation.id == emp.org_id))).scalar_one()
        assert org.require_mfa is False


@pytest.mark.asyncio
async def test_login_flags_enrollment_when_required_and_not_enrolled(client):
    _, hdr = await _owner(client, "flag@acme.dev")
    await _require(client, hdr)
    r = await client.post("/api/auth/token", data={"username": "flag@acme.dev", "password": "owner-pass-123"})
    assert r.status_code == 200
    assert r.json()["mfa_enrollment_required"] is True
    # /me exposes the org policy
    me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"})
    assert me.json()["mfa_required"] is True
    assert me.json()["mfa_enabled"] is False


@pytest.mark.asyncio
async def test_cannot_self_disable_when_org_requires_mfa(client):
    _, hdr = await _owner(client, "nodisable@acme.dev")
    await _require(client, hdr)
    # even without being enrolled, the disable endpoint must refuse under mandatory policy
    r = await client.post("/api/mfa/disable", json={"password": "owner-pass-123"}, headers=hdr)
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_admin_can_reset_teammate_mfa(client):
    data, hdr = await _owner(client, "admin2@acme.dev")
    # add a teammate
    emp = await client.post("/api/staff/employees",
                            json={"name": "Tech", "email": "tech2@acme.dev", "password": "tech-pass-123"}, headers=hdr)
    emp_id = emp.json()["id"]
    # simulate them having 2FA on
    async with AsyncSessionLocal() as db:
        e = (await db.execute(select(Employee).where(Employee.id == emp_id))).scalar_one()
        e.mfa_enabled = True
        e.mfa_secret = "ABCDEFGHIJKLMNOP"
        await db.commit()
    r = await client.post(f"/api/staff/employees/{emp_id}/reset-mfa", json={}, headers=hdr)
    assert r.status_code == 200
    async with AsyncSessionLocal() as db:
        e = (await db.execute(select(Employee).where(Employee.id == emp_id))).scalar_one()
        assert e.mfa_enabled is False and e.mfa_secret is None


@pytest.mark.asyncio
async def test_policy_can_be_turned_off_then_disable_works(client):
    _, hdr = await _owner(client, "toggle@acme.dev")
    # turn the org policy off
    await client.put("/api/staff/company", json={"require_mfa": False}, headers=hdr)
    me = await client.get("/api/auth/me", headers=hdr)
    assert me.json()["mfa_required"] is False
    # now disabling (already-off) 2FA is allowed (password checked)
    r = await client.post("/api/mfa/disable", json={"password": "owner-pass-123"}, headers=hdr)
    assert r.status_code == 200
