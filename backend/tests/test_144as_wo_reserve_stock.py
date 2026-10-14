"""144as — reserve on-hand stock for a work order (a timed hold), the companion to raising a PO.
Reserve earmarks what's in stock; the PO covers the shortfall. Hold auto-expires."""
import pytest


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _part_with_stock(client, h, loc, name, sku, qty):
    pid = (await client.post("/api/inventory/parts", json={"name": name, "sku": sku}, headers=h)).json()["id"]
    await client.post(f"/api/inventory/parts/{pid}/adjust", headers=h,
                      json={"location_id": loc, "qty": qty, "movement_type": "receive"})
    return pid


@pytest.mark.asyncio
async def test_reserve_holds_in_stock_parts_for_the_job(client, tenant):
    loc = await _loc(client, tenant)
    pid = await _part_with_stock(client, tenant, loc, "Battery", "RS-1", 10)
    cid = (await client.post("/api/clients", json={"name": "Owner"}, headers=tenant)).json()["id"]
    # an estimate (doesn't auto-consume stock) that needs 3 of the part
    oid = (await client.post("/api/orders", json={
        "client_id": cid, "location_id": loc, "is_estimate": True, "complaint": "swap",
        "lines": [{"line_type": "part", "kind": "product", "ref_id": pid, "description": "Battery", "qty": 3, "unit_price": 40}]},
        headers=tenant)).json()["id"]

    r = (await client.post(f"/api/orders/{oid}/reserve-stock", json={"hold_days": 5}, headers=tenant)).json()
    assert r["reserved"] == [{"part": "Battery", "qty": 3}]
    assert r["hold_days"] == 5

    # the hold shows on the order, and 3 units are now reserved on the stock level
    held = (await client.get(f"/api/orders/{oid}/reservations", headers=tenant)).json()["items"]
    assert len(held) == 1 and held[0]["qty"] == 3
    prod = (await client.get(f"/api/inventory/parts/{pid}", headers=tenant)).json()
    assert prod["qty_reserved"] == 3

    # reserving again is idempotent against what's already held for THIS job (no double-hold)
    r2 = (await client.post(f"/api/orders/{oid}/reserve-stock", json={"hold_days": 5}, headers=tenant)).json()
    assert r2["reserved"] == []
    prod2 = (await client.get(f"/api/inventory/parts/{pid}", headers=tenant)).json()
    assert prod2["qty_reserved"] == 3

    # release frees it
    rid = held[0]["id"]
    assert (await client.post(f"/api/inventory/reservations/{rid}/release", headers=tenant)).status_code == 200
    assert (await client.get(f"/api/orders/{oid}/reservations", headers=tenant)).json()["items"] == []
    prod3 = (await client.get(f"/api/inventory/parts/{pid}", headers=tenant)).json()
    assert prod3["qty_reserved"] == 0


@pytest.mark.asyncio
async def test_reserve_reports_shortfall_when_stock_is_thin(client, tenant):
    loc = await _loc(client, tenant)
    pid = await _part_with_stock(client, tenant, loc, "Hose", "RS-2", 1)   # only 1 free
    cid = (await client.post("/api/clients", json={"name": "Owner2"}, headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", json={
        "client_id": cid, "location_id": loc, "is_estimate": True,
        "lines": [{"line_type": "part", "kind": "product", "ref_id": pid, "description": "Hose", "qty": 4, "unit_price": 5}]},
        headers=tenant)).json()["id"]
    r = (await client.post(f"/api/orders/{oid}/reserve-stock", json={"hold_days": 7}, headers=tenant)).json()
    assert r["reserved"] == [{"part": "Hose", "qty": 1}]       # held what was free
    assert any(s["part"] == "Hose" and s["needed"] == 3 for s in r["short"])  # 3 still short → raise a PO
