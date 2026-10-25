"""Workstream B — continue as a linked job (gap #4): from any job, open or closed, start a new job
for the same customer and device, linked both ways."""
import uuid

import pytest

pytestmark = pytest.mark.asyncio


async def _job(client, H):
    cid = (await client.post("/api/clients", json={"name": "Linda Link"}, headers=H)).json()["id"]
    return (await client.post("/api/orders", json={"client_id": cid, "complaint": "No power"}, headers=H)).json()


async def test_continue_a_closed_job_as_a_linked_job(client, tenant, db):
    o = await _job(client, tenant)
    assert (await client.patch(f"/api/orders/{o['id']}/status", json={"status": "done"}, headers=tenant)).status_code == 200
    r = await client.post(f"/api/orders/{o['id']}/continue", json={"complaint": "Came back: no sound"}, headers=tenant)
    assert r.status_code == 201, r.text
    new = r.json()
    assert new["order_number"] != o["order_number"]
    got = (await client.get(f"/api/orders/{new['id']}", headers=tenant)).json()
    orig = (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()
    assert got["client"]["id"] == orig["client"]["id"]
    from sqlalchemy import select

    from backend.models import Order
    rows = {r.id: r for r in (await db.execute(select(Order).where(Order.id.in_([o["id"], new["id"]])))).scalars()}
    assert rows[new["id"]].location_id == rows[o["id"]].location_id is not None
    assert got["complaint"] == "Came back: no sound"
    assert got["continued_from_order_id"] == o["id"]
    assert got["closed_at"] is None
    links = (await client.get(f"/api/orders/{o['id']}/linked", headers=tenant)).json()
    assert [x["id"] for x in links["continued_in"]] == [new["id"]]
    back = (await client.get(f"/api/orders/{new['id']}/linked", headers=tenant)).json()
    assert back["continued_from"]["id"] == o["id"]


async def test_complaint_defaults_to_the_original(client, tenant):
    o = await _job(client, tenant)
    r = await client.post(f"/api/orders/{o['id']}/continue", json={}, headers=tenant)
    assert r.status_code == 201, r.text
    got = (await client.get(f"/api/orders/{r.json()['id']}", headers=tenant)).json()
    assert got["complaint"] == "No power"


async def test_another_shops_job_cannot_be_continued(client, tenant):
    o = await _job(client, tenant)
    email = f"o-{uuid.uuid4().hex[:6]}@t.dev"
    tok = (await client.post("/api/auth/register", json={"org_name": "Other", "name": "O", "email": email,
                                                         "password": "pw-test-12345"})).json()["access_token"]
    H2 = {"Authorization": f"Bearer {tok}"}
    assert (await client.post(f"/api/orders/{o['id']}/continue", json={}, headers=H2)).status_code == 404
    assert (await client.get(f"/api/orders/{o['id']}/linked", headers=H2)).status_code == 404


async def test_needs_the_create_jobs_permission(client, tenant):
    o = await _job(client, tenant)
    role = (await client.post("/api/staff/roles", json={"name": "Viewer", "permissions": {"can_create_tickets": False}},
                              headers=tenant)).json()
    em = f"v-{uuid.uuid4().hex[:6]}@t.dev"
    await client.post("/api/staff/employees", json={"name": "V", "email": em, "password": "pw-test-12345",
                                                     "role_id": role["id"]}, headers=tenant)
    tok = (await client.post("/api/auth/token", data={"username": em, "password": "pw-test-12345"})).json()
    H = {"Authorization": f"Bearer {tok['access_token']}"}
    assert (await client.post(f"/api/orders/{o['id']}/continue", json={}, headers=H)).status_code == 403


async def test_estimates_cannot_be_continued(client, tenant):
    e = (await client.post("/api/orders", json={"complaint": "Quote", "is_estimate": True}, headers=tenant)).json()
    r = await client.post(f"/api/orders/{e['id']}/continue", json={}, headers=tenant)
    assert r.status_code == 409
