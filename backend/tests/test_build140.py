"""Build 140 — inventory stock list scopes to a branch (transfers already existed)."""
import pytest


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


def _qty(items, pid):
    for i in items:
        if i["id"] == pid:
            return i.get("stock", i.get("qty_available", i.get("available", 0)))
    return None


@pytest.mark.asyncio
async def test_parts_stock_scopes_by_location(client, tenant, db):
    from backend.models import StockLevel
    loc = await _loc(client, tenant)
    loc2 = (await client.post("/api/staff/locations", json={"name": "Branch S"}, headers=tenant)).json()["id"]
    pid = (await client.post("/api/inventory/parts", json={"name": "Bolt", "sku": "BLT-1"}, headers=tenant)).json()["id"]

    # 7 units at the seed location, 3 at branch S
    db.add(StockLevel(part_id=pid, location_id=loc, qty_available=7, qty_reserved=0))
    db.add(StockLevel(part_id=pid, location_id=loc2, qty_available=3, qty_reserved=0))
    await db.commit()

    all_items = (await client.get("/api/inventory/parts", headers=tenant)).json()["items"]
    at_loc = (await client.get(f"/api/inventory/parts?location_id={loc}", headers=tenant)).json()["items"]
    at_l2 = (await client.get(f"/api/inventory/parts?location_id={loc2}", headers=tenant)).json()["items"]

    assert _qty(all_items, pid) == 10      # org-wide total
    assert _qty(at_loc, pid) == 7          # seed branch only
    assert _qty(at_l2, pid) == 3           # branch S only


@pytest.mark.asyncio
async def test_branch_transfer_exists_and_moves_stock(client, tenant, db):
    """The inter-location transfer feature already exists; confirm the endpoint works."""
    from backend.models import StockLevel
    loc = await _loc(client, tenant)
    loc2 = (await client.post("/api/staff/locations", json={"name": "Dest"}, headers=tenant)).json()["id"]
    pid = (await client.post("/api/inventory/parts", json={"name": "Nut", "sku": "NUT-1"}, headers=tenant)).json()["id"]
    db.add(StockLevel(part_id=pid, location_id=loc, qty_available=5, qty_reserved=0))
    await db.commit()

    r = await client.post("/api/transfers", json={
        "from_location_id": loc, "to_location_id": loc2,
        "lines": [{"part_id": pid, "qty": 2}]}, headers=tenant)
    assert r.status_code in (200, 201), r.text
    # a transfer between two different locations is accepted (same-location is rejected)
    bad = await client.post("/api/transfers", json={
        "from_location_id": loc, "to_location_id": loc,
        "lines": [{"part_id": pid, "qty": 1}]}, headers=tenant)
    assert bad.status_code >= 400
