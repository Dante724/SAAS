"""Build 137 — global location scope: dashboard filters by location_id."""
import pytest


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


@pytest.mark.asyncio
async def test_dashboard_scopes_to_location(client, tenant):
    loc = await _loc(client, tenant)
    cid = (await client.post("/api/clients", json={"name": "L", "phone": "+1"}, headers=tenant)).json()["id"]
    # an order at the seed location
    await client.post("/api/orders", json={"location_id": loc, "complaint": "x", "client_id": cid}, headers=tenant)

    # a second location with no orders
    loc2 = (await client.post("/api/staff/locations", json={"name": "Empty branch"}, headers=tenant)).json()["id"]

    alld = (await client.get("/api/reports/company-dashboard", headers=tenant)).json()["monthly"]
    at_loc = (await client.get(f"/api/reports/company-dashboard?location_id={loc}", headers=tenant)).json()["monthly"]
    empty = (await client.get(f"/api/reports/company-dashboard?location_id={loc2}", headers=tenant)).json()["monthly"]

    # the empty branch sees no work orders; the seed branch and "all" do
    assert empty["work_orders_created"] == 0
    assert at_loc["work_orders_created"] >= 1
    assert alld["work_orders_created"] >= at_loc["work_orders_created"]


@pytest.mark.asyncio
async def test_dashboard_all_locations_default(client, tenant):
    # no param → org-wide (unchanged behaviour), still returns the KPI shape
    k = (await client.get("/api/reports/company-dashboard", headers=tenant)).json()["kpis"]
    assert "new" in k and "ar" in k
