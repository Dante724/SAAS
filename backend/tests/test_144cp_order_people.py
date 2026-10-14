"""144cp — WO Manager + Specialist (assignee) on the Overview.

The order now carries manager_id (owner) distinct from assigned_to_id
(the specialist doing the work). The /assign endpoint sets either or both.
"""
import uuid
import pytest


async def _register(client):
    email = f"a-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Co", "name": "Admin", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, r.json()


async def _first_location(client, H):
    loc = (await client.get("/api/staff/locations", headers=H)).json()
    return loc[0]["id"] if isinstance(loc, list) else loc["items"][0]["id"]


async def _employees(client, H):
    r = await client.get("/api/staff/employees", headers=H)
    data = r.json()
    return data if isinstance(data, list) else data.get("items", [])


@pytest.mark.asyncio
async def test_assign_sets_manager_and_specialist(client):
    H, _ = await _register(client)
    loc_id = await _first_location(client, H)
    emps = await _employees(client, H)
    assert emps, "the registering admin should exist as an employee"
    me = emps[0]["id"]

    o = (await client.post("/api/orders", json={"location_id": loc_id, "complaint": "x"}, headers=H)).json()

    r = await client.patch(f"/api/orders/{o['id']}/assign",
                           json={"manager_id": me, "assigned_to_id": me}, headers=H)
    assert r.status_code == 200, r.text

    got = (await client.get(f"/api/orders/{o['id']}", headers=H)).json()
    assert got["manager"] and got["manager"]["id"] == me
    assert got["assigned_to"] and got["assigned_to"]["id"] == me


@pytest.mark.asyncio
async def test_assign_only_touches_sent_fields(client):
    H, _ = await _register(client)
    loc_id = await _first_location(client, H)
    me = (await _employees(client, H))[0]["id"]
    o = (await client.post("/api/orders", json={"location_id": loc_id, "complaint": "x"}, headers=H)).json()

    # set manager only
    await client.patch(f"/api/orders/{o['id']}/assign", json={"manager_id": me}, headers=H)
    # now clear assignee explicitly, manager must remain
    await client.patch(f"/api/orders/{o['id']}/assign", json={"assigned_to_id": ""}, headers=H)
    got = (await client.get(f"/api/orders/{o['id']}", headers=H)).json()
    assert got["manager"] and got["manager"]["id"] == me
    assert got["assigned_to"] is None
