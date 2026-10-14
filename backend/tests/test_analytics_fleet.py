"""A9 Owner analytics + A8 Fleet/B2B accounts."""
import pytest
from sqlalchemy import select


async def _loc(client, tenant):
    return (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]


async def _closed_order(client, tenant, db, *, client_id=None, device_id=None,
                        assignee=None, total_line=100.0):
    from datetime import datetime, timedelta

    from backend.models import Order
    loc = await _loc(client, tenant)
    body = {"location_id": loc, "complaint": "x"}
    if client_id:
        body["client_id"] = client_id
    if device_id:
        body["device_id"] = device_id
    if assignee:
        body["assigned_to_id"] = assignee
    o = (await client.post("/api/orders", json=body, headers=tenant)).json()
    # backdate created 2 days
    row = (await db.execute(select(Order).where(Order.id == o["id"]))).scalar_one()
    row.created_at = datetime.utcnow() - timedelta(days=2)
    await db.commit()
    await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "labour", "kind": "custom", "description": "job",
        "qty": 1, "unit_price": total_line}, headers=tenant)
    await client.patch(f"/api/orders/{o['id']}/status", json={"status": "closed-an"}, headers=tenant)
    return o["id"]


@pytest.fixture
async def _terminal_status(client, tenant):
    r = await client.post("/api/statuses", json={
        "name": "Closed AN", "key": "closed-an", "status_group": "closed", "is_terminal": True}, headers=tenant)
    assert r.status_code == 201


@pytest.mark.asyncio
async def test_technician_productivity(client, tenant, db, _terminal_status):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    await _closed_order(client, tenant, db, assignee=me["id"], total_line=200.0)
    await _closed_order(client, tenant, db, assignee=me["id"], total_line=100.0)

    r = (await client.get("/api/analytics/technician-productivity?days=30", headers=tenant)).json()
    mine = next(t for t in r["technicians"] if t["technician_id"] == me["id"])
    assert mine["jobs_closed"] == 2
    assert mine["revenue"] == pytest.approx(300.0)
    assert mine["avg_turnaround_hours"] == pytest.approx(48.0, abs=1.0)   # ~2 days


@pytest.mark.asyncio
async def test_location_comparison_and_trends(client, tenant, db, _terminal_status):
    await _closed_order(client, tenant, db, total_line=150.0)
    comp = (await client.get("/api/analytics/location-comparison?days=30", headers=tenant)).json()
    assert comp["locations"] and comp["locations"][0]["orders_closed"] >= 1
    assert comp["locations"][0]["close_rate"] > 0

    tr = (await client.get("/api/analytics/trends?days=30", headers=tenant)).json()
    assert any(pt["closed"] >= 1 for pt in tr["series"])


@pytest.mark.asyncio
async def test_fleet_overview_and_detail(client, tenant, db, _terminal_status):
    # company (fleet) client with 2 vehicles
    cl = (await client.post("/api/clients",
                           json={"name": "Acme Fleet", "client_type": "company"}, headers=tenant)).json()
    cid = cl["id"]
    d1 = (await client.post(f"/api/clients/{cid}/devices",
                           json={"brand": "Ford", "model": "Transit", "asset_type": "Truck", "vin": "1FTBW2CM5..."},
                           headers=tenant)).json()
    await client.post(f"/api/clients/{cid}/devices",
                      json={"brand": "Ram", "model": "ProMaster", "asset_type": "Truck"}, headers=tenant)
    dev_id = d1.get("id") or (await client.get(f"/api/clients/{cid}", headers=tenant)).json()["devices"][0]["id"]

    await _closed_order(client, tenant, db, client_id=cid, device_id=dev_id, total_line=500.0)

    overview = (await client.get("/api/analytics/fleet", headers=tenant)).json()
    acme = next(c for c in overview["companies"] if c["client_id"] == cid)
    assert acme["vehicle_count"] == 2

    detail = (await client.get(f"/api/analytics/fleet/{cid}", headers=tenant)).json()
    assert detail["vehicle_count"] == 2
    assert detail["consolidated_spend"] == pytest.approx(500.0)
    serviced = next(v for v in detail["vehicles"] if v["device_id"] == dev_id)
    assert serviced["lifetime_spend"] == pytest.approx(500.0) and serviced["service_count"] == 1
