"""A8 Fleet/B2B + A9 Business Insights — net-new depth on top of existing analytics."""
import pytest
from sqlalchemy import select


async def _loc(client, tenant):
    return (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]


async def _terminal_status(client, tenant, key="done-ai"):
    r = await client.post("/api/statuses", json={
        "name": key, "key": key, "status_group": "closed", "is_terminal": True}, headers=tenant)
    assert r.status_code in (201, 409)
    return key


@pytest.mark.asyncio
async def test_fleet_accepts_both_type_values(client, tenant):
    """Regression: fleet must find company clients whether typed 'company' or
    'organization' (the model comment and the query disagreed)."""
    for t in ("company", "organization"):
        await client.post("/api/clients", json={"name": f"Fleet {t}", "client_type": t}, headers=tenant)
    fleet = (await client.get("/api/analytics/fleet", headers=tenant)).json()["companies"]
    names = {c["name"] for c in fleet}
    assert "Fleet company" in names and "Fleet organization" in names


@pytest.mark.asyncio
async def test_fleet_statement(client, tenant, db):
    from backend.models import Device
    loc = await _loc(client, tenant)
    term = await _terminal_status(client, tenant)
    cid = (await client.post("/api/clients",
                             json={"name": "Acme Fleet", "client_type": "organization"}, headers=tenant)).json()["id"]
    # two vehicles, one closed order each
    for vin in ("VIN-A", "VIN-B"):
        dev = Device(client_id=cid, owner_type="client", asset_type="Vehicle", brand="Ford", model="Transit", vin=vin)
        db.add(dev)
        await db.commit()
        await db.refresh(dev)
        o = (await client.post("/api/orders", json={
            "location_id": loc, "client_id": cid, "complaint": "svc", "device_id": dev.id}, headers=tenant)).json()
        await client.post(f"/api/orders/{o['id']}/lines", json={
            "line_type": "labour", "kind": "custom", "description": "service", "qty": 1, "unit_price": 100}, headers=tenant)
        await client.patch(f"/api/orders/{o['id']}/status", json={"status": term}, headers=tenant)

    stmt = (await client.get(f"/api/analytics/fleet/{cid}/statement", headers=tenant)).json()
    assert stmt["line_count"] == 2
    assert stmt["total"] == pytest.approx(200.0)
    assert {ln["vehicle"] for ln in stmt["lines"]} == {"Ford Transit"}

    detail = (await client.get(f"/api/analytics/fleet/{cid}", headers=tenant)).json()
    assert detail["vehicle_count"] == 2 and detail["consolidated_spend"] == pytest.approx(200.0)


@pytest.mark.asyncio
async def test_business_insights(client, tenant):
    loc = await _loc(client, tenant)
    term = await _terminal_status(client, tenant, "done-bi")
    me = (await client.get("/api/auth/me", headers=tenant)).json()

    # one closed order (revenue 150), one still open
    o1 = (await client.post("/api/orders", json={"location_id": loc, "complaint": "x",
                                                 "assigned_to_id": me["id"]}, headers=tenant)).json()
    await client.post(f"/api/orders/{o1['id']}/lines", json={
        "line_type": "labour", "kind": "custom", "description": "Diagnostics",
        "qty": 1, "unit_price": 150, "technician_id": me["id"]}, headers=tenant)
    await client.patch(f"/api/orders/{o1['id']}/status", json={"status": term}, headers=tenant)
    await client.post("/api/orders", json={"location_id": loc, "complaint": "open"}, headers=tenant)

    bi = (await client.get("/api/analytics/business-insights?days=30", headers=tenant)).json()
    assert bi["orders_created"] >= 2
    assert bi["orders_closed"] >= 1
    assert bi["revenue"] >= 150.0
    assert bi["avg_ticket_value"] >= 150.0
    assert 0 < bi["close_rate_pct"] <= 100
    # top tech + top service reflect the closed order
    assert any(t["name"] == me["name"] and t["revenue"] >= 150 for t in bi["top_technicians"])
    assert any(s["service"] == "Diagnostics" for s in bi["top_services"])
