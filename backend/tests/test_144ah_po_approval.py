"""144ah — PO spend-approval gate: over-threshold POs need a manager to send."""
import pytest
from sqlalchemy import select

from backend.models import Employee, Organisation, Role, Supplier
from backend.services.auth import hash_password


async def _login(client, email, pw):
    r = await client.post("/api/auth/token", data={"username": email, "password": pw})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _po(client, tenant, sid, loc, cost):
    pid = (await client.post("/api/inventory/parts", json={"name": f"P{cost}", "sku": f"S{cost}"}, headers=tenant)).json()["id"]
    return (await client.post("/api/inventory/po", headers=tenant, json={
        "supplier_id": sid, "location_id": loc, "lines": [{"part_id": pid, "qty_ordered": 1, "unit_cost": cost}]})).json()["po_id"]


@pytest.mark.asyncio
async def test_over_threshold_needs_manager(client, tenant, db):
    org = (await db.execute(select(Organisation))).scalars().first()
    admin = (await db.execute(select(Employee).where(Employee.org_id == org.id))).scalars().first()
    org.po_approval_threshold = 100
    # restricted role: explicitly denied settings management (a buyer, not a manager)
    role = Role(org_id=org.id, name="Buyer", permissions={"can_manage_settings": False})
    db.add(role); await db.flush()
    buyer = Employee(org_id=org.id, location_id=admin.location_id, role_id=role.id,
                     name="Buyer", email="buyer@appr.test", password_hash=hash_password("pw-buyer-123"), active=True)
    db.add(buyer); await db.commit()
    sid = (await client.post("/api/suppliers", json={"name": "Acme"}, headers=tenant)).json()["id"]
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]

    big = await _po(client, tenant, sid, loc, 250)     # > 100
    small = await _po(client, tenant, sid, loc, 40)    # < 100
    bh = await _login(client, "buyer@appr.test", "pw-buyer-123")

    # buyer blocked on the big PO, allowed on the small one
    assert (await client.post(f"/api/inventory/po/{big}/send", headers=bh, json={"channel": "email"})).status_code == 403
    assert (await client.post(f"/api/inventory/po/{small}/send", headers=bh, json={"channel": "email"})).status_code == 200
    # admin (no restricted role) can send the big one
    assert (await client.post(f"/api/inventory/po/{big}/send", headers=tenant, json={"channel": "email"})).status_code == 200


@pytest.mark.asyncio
async def test_threshold_roundtrips_through_settings(client, tenant):
    await client.put("/api/staff/inventory-settings", headers=tenant, json={"po_approval_threshold": 500})
    got = (await client.get("/api/staff/inventory-settings", headers=tenant)).json()
    assert got["po_approval_threshold"] == 500
