"""Build 144ch — instant suspension kills live sessions; signup maps currency
codes to display symbols."""
import pytest
from sqlalchemy import select

from backend.db.database import AsyncSessionLocal
from backend.models import Employee, Organisation, PlatformAdmin
from backend.services.auth import hash_password


async def _admin_hdr(client):
    from backend.tests.admin_helpers import admin_headers
    return (await admin_headers(client))[0]


@pytest.mark.asyncio
async def test_suspension_kills_live_session(client):
    hdr = await _admin_hdr(client)
    reg = await client.post("/api/auth/register", json={
        "org_name": "LiveCo", "name": "Owner", "email": "live@ch.dev", "password": "pw-12345678"})
    ttok = reg.json()["access_token"]
    thdr = {"Authorization": f"Bearer {ttok}"}
    # the live token works before suspension
    assert (await client.get("/api/auth/me", headers=thdr)).status_code == 200
    oid = reg.json()["org_id"]
    # suspend from the operator portal
    await client.post(f"/api/admin/tenants/{oid}/suspend", json={"reason": "Unpaid"}, headers=hdr)
    # the SAME live token is now rejected immediately (token version bumped)
    assert (await client.get("/api/auth/me", headers=thdr)).status_code == 401


@pytest.mark.asyncio
async def test_expiring_in_past_kills_live_session(client):
    hdr = await _admin_hdr(client)
    reg = await client.post("/api/auth/register", json={
        "org_name": "ExpLiveCo", "name": "Owner", "email": "explive@ch.dev", "password": "pw-12345678"})
    thdr = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    assert (await client.get("/api/auth/me", headers=thdr)).status_code == 200
    await client.patch(f"/api/admin/tenants/{reg.json()['org_id']}", json={"sub_end": "2020-01-01", "reason": "Ended"}, headers=hdr)
    assert (await client.get("/api/auth/me", headers=thdr)).status_code == 401


@pytest.mark.asyncio
async def test_reactivating_does_not_bump_needlessly(client):
    hdr = await _admin_hdr(client)
    reg = await client.post("/api/auth/register", json={
        "org_name": "ActiveCo", "name": "Owner", "email": "active@ch.dev", "password": "pw-12345678"})
    thdr = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    # setting status to active (not blocking) must NOT invalidate the live token
    await client.patch(f"/api/admin/tenants/{reg.json()['org_id']}", json={"sub_status": "active", "reason": "Paid"}, headers=hdr)
    assert (await client.get("/api/auth/me", headers=thdr)).status_code == 200


@pytest.mark.asyncio
async def test_signup_maps_currency_code_to_symbol(client):
    await client.post("/api/auth/register", json={
        "org_name": "PoundCo", "name": "P", "email": "pound@ch.dev", "password": "pw-12345678",
        "region": "eu", "currency_code": "GBP"})
    async with AsyncSessionLocal() as db:
        emp = (await db.execute(select(Employee).where(Employee.email == "pound@ch.dev"))).scalar_one()
        org = (await db.execute(select(Organisation).where(Organisation.id == emp.org_id))).scalar_one()
        assert org.currency_code == "GBP"
        assert org.currency == "£"   # mapped, not the bare code


@pytest.mark.asyncio
async def test_unknown_currency_code_falls_back_to_itself(client):
    await client.post("/api/auth/register", json={
        "org_name": "XyzCo", "name": "X", "email": "xyz@ch.dev", "password": "pw-12345678",
        "currency_code": "XYZ"})
    async with AsyncSessionLocal() as db:
        emp = (await db.execute(select(Employee).where(Employee.email == "xyz@ch.dev"))).scalar_one()
        org = (await db.execute(select(Organisation).where(Organisation.id == emp.org_id))).scalar_one()
        assert org.currency == "XYZ"
