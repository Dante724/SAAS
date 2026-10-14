"""Security: a shop can't attach another shop's records by id (cross-tenant references).

Regression for the audit finding where ids taken from request bodies (client_id, device_id,
assignee, part_id, supplier_id…) were never checked against the caller's org, so org A could
create an order/invoice/task pointing at org B's client — and then read it back through the
relationship, message it, or move its balance. Enforced centrally by backend/db/tenancy.py.
"""
import uuid

import pytest
from sqlalchemy import select

from backend.models import Client, Order


async def _register(client):
    email = f"xt-{uuid.uuid4().hex[:8]}@test.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Org B", "name": "Owner B", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


@pytest.fixture
async def org_b(client):
    hb = await _register(client)
    cid = (await client.post("/api/clients", json={"name": "Victim B", "phone": "+15550199"}, headers=hb)).json()["id"]
    me = (await client.get("/api/auth/me", headers=hb)).json()
    return {"h": hb, "client_id": cid, "owner_id": me["id"], "loc": await _loc(client, hb)}


@pytest.mark.asyncio
async def test_cannot_create_order_for_another_shops_client(client, tenant, db, org_b):
    r = await client.post("/api/orders", headers=tenant, json={
        "client_id": org_b["client_id"], "location_id": await _loc(client, tenant), "complaint": "x"})
    assert r.status_code == 404
    leaked = (await db.execute(select(Order).where(Order.client_id == org_b["client_id"]))).scalars().all()
    assert leaked == []


@pytest.mark.asyncio
async def test_cannot_use_another_shops_location(client, tenant, org_b):
    cid = (await client.post("/api/clients", json={"name": "Mine"}, headers=tenant)).json()["id"]
    r = await client.post("/api/orders", headers=tenant, json={
        "client_id": cid, "location_id": org_b["loc"], "complaint": "x"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_cannot_repoint_existing_order_at_foreign_employee(client, tenant, org_b):
    cid = (await client.post("/api/clients", json={"name": "Mine"}, headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", headers=tenant, json={
        "client_id": cid, "location_id": await _loc(client, tenant), "complaint": "x"})).json()["id"]
    r = await client.patch(f"/api/orders/{oid}/assign", headers=tenant, json={"assigned_to_id": org_b["owner_id"]})
    assert r.status_code == 404
    o = (await client.get(f"/api/orders/{oid}", headers=tenant)).json()
    assert o.get("assigned_to_id") != org_b["owner_id"]


@pytest.mark.asyncio
async def test_cannot_invoice_another_shops_client(client, tenant, org_b):
    r = await client.post("/api/finance/invoices", headers=tenant, json={"client_id": org_b["client_id"]})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_cannot_assign_task_to_another_shops_employee(client, tenant, org_b):
    r = await client.post("/api/tasks", headers=tenant, json={"title": "t", "assigned_to_id": org_b["owner_id"]})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_foreign_client_balance_untouched(client, tenant, db, org_b):
    """The ledger path that moved another tenant's client balance."""
    await client.post("/api/finance/invoices", headers=tenant, json={"client_id": org_b["client_id"]})
    c = (await db.execute(select(Client).where(Client.id == org_b["client_id"]))).scalar_one()
    assert float(c.balance or 0) == 0


@pytest.mark.asyncio
async def test_same_shop_references_still_work(client, tenant):
    cid = (await client.post("/api/clients", json={"name": "Mine"}, headers=tenant)).json()["id"]
    r = await client.post("/api/orders", headers=tenant, json={
        "client_id": cid, "location_id": await _loc(client, tenant), "complaint": "x"})
    assert r.status_code in (200, 201), r.text


@pytest.mark.asyncio
async def test_reassigning_an_order_notifies_the_new_technician(client, tenant, db):
    """Regression: the assign endpoint called an un-imported notify() with the wrong arguments,
    and the bare except hid it, so reassigned technicians were never alerted."""
    from backend.models import Employee, StaffNotification
    from backend.services.auth import hash_password
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    owner = (await db.execute(select(Employee).where(Employee.id == me["id"]))).scalar_one()
    tech = Employee(org_id=owner.org_id, location_id=owner.location_id, name="Tech",
                    email=f"t-{uuid.uuid4().hex[:6]}@xt.test", password_hash=hash_password("pw-t-123456"), active=True)
    db.add(tech)
    await db.commit()
    cid = (await client.post("/api/clients", json={"name": "Mine"}, headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", headers=tenant, json={
        "client_id": cid, "location_id": await _loc(client, tenant), "complaint": "x"})).json()["id"]
    assert (await client.patch(f"/api/orders/{oid}/assign", headers=tenant,
                               json={"assigned_to_id": tech.id})).status_code == 200
    notes = (await db.execute(select(StaffNotification).where(StaffNotification.employee_id == tech.id))).scalars().all()
    assert any(n.kind == "job.assigned" for n in notes)
