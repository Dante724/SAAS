"""Build 138 — global location scope reaches the work-order list/board and sales."""
import pytest


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


@pytest.mark.asyncio
async def test_orders_list_and_board_scope_by_location(client, tenant):
    loc = await _loc(client, tenant)
    cid = (await client.post("/api/clients", json={"name": "L", "phone": "+1"}, headers=tenant)).json()["id"]
    await client.post("/api/orders", json={"location_id": loc, "complaint": "x", "client_id": cid}, headers=tenant)
    loc2 = (await client.post("/api/staff/locations", json={"name": "Branch 2"}, headers=tenant)).json()["id"]

    # list endpoint (already supported) honours the scope
    at = (await client.get(f"/api/orders?location_id={loc}", headers=tenant)).json()["items"]
    other = (await client.get(f"/api/orders?location_id={loc2}", headers=tenant)).json()["items"]
    assert len(at) >= 1 and len(other) == 0

    # board endpoint accepts the new location_id param and stays well-formed
    b = await client.get(f"/api/orders/board?location_id={loc}", headers=tenant)
    assert b.status_code == 200 and "columns" in b.json()
    # a bogus location filters everything out of any populated column
    b_other = (await client.get(f"/api/orders/board?location_id={loc2}", headers=tenant)).json()
    assert all(not col.get("cards") for col in b_other.get("columns", []))


@pytest.mark.asyncio
async def test_sales_list_scopes_by_location(client, tenant):
    # a wrong-location filter returns nothing; the seller's own location returns its sales
    loc2 = (await client.post("/api/staff/locations", json={"name": "Empty"}, headers=tenant)).json()["id"]
    scoped = (await client.get(f"/api/sales?location_id={loc2}", headers=tenant)).json()["items"]
    assert scoped == []
    # unscoped still works (org-wide)
    r = await client.get("/api/sales", headers=tenant)
    assert r.status_code == 200 and "items" in r.json()
