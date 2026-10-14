"""Build 141 — location scope reaches Reports (wo_created) and Purchase Orders."""
import pytest


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


@pytest.mark.asyncio
async def test_wo_report_scopes_by_location(client, tenant):
    loc = await _loc(client, tenant)
    loc2 = (await client.post("/api/staff/locations", json={"name": "R2"}, headers=tenant)).json()["id"]
    cid = (await client.post("/api/clients", json={"name": "R", "phone": "+1"}, headers=tenant)).json()["id"]
    await client.post("/api/orders", json={"location_id": loc, "complaint": "x", "client_id": cid}, headers=tenant)

    allr = (await client.get("/api/reports/run/wo_created", headers=tenant)).json()
    at = (await client.get(f"/api/reports/run/wo_created?location_id={loc}", headers=tenant)).json()
    other = (await client.get(f"/api/reports/run/wo_created?location_id={loc2}", headers=tenant)).json()

    assert at["row_count"] >= 1
    assert other["row_count"] == 0
    assert allr["row_count"] >= at["row_count"]


@pytest.mark.asyncio
async def test_po_list_scopes_by_location(client, tenant):
    loc = await _loc(client, tenant)
    loc2 = (await client.post("/api/staff/locations", json={"name": "PO branch"}, headers=tenant)).json()["id"]
    sid = (await client.post("/api/suppliers", json={"name": "Sup", "phone": "+19"}, headers=tenant)).json()["id"]
    pid = (await client.post("/api/inventory/parts", json={"name": "Widget", "sku": "WP-1"}, headers=tenant)).json()["id"]
    await client.post("/api/inventory/po", json={
        "supplier_id": sid, "location_id": loc,
        "lines": [{"part_id": pid, "qty_ordered": 1, "unit_cost": 10}]}, headers=tenant)

    at = (await client.get(f"/api/inventory/po?location_id={loc}", headers=tenant)).json()["items"]
    other = (await client.get(f"/api/inventory/po?location_id={loc2}", headers=tenant)).json()["items"]
    assert len(at) >= 1 and len(other) == 0
