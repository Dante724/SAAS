"""144cz — a job kit (bundle) applied to a real work order deducts stock for its PRODUCT
components (service components don't); estimates never deduct."""
import pytest


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _part(client, h, loc, qty):
    pid = (await client.post("/api/inventory/parts", json={"name": "Screen", "sku": "SCR-1"}, headers=h)).json()["id"]
    await client.post(f"/api/inventory/parts/{pid}/adjust", headers=h,
                      json={"location_id": loc, "qty": qty, "movement_type": "receive"})
    return pid


async def _kit(client, h, comps):
    return (await client.post("/api/bundles", json={
        "name": "Screen combo", "price_mode": "sum", "price": 0, "components": comps}, headers=h)).json()["id"]


@pytest.mark.asyncio
async def test_jobkit_on_work_order_deducts_product_stock(client, tenant):
    loc = await _loc(client, tenant)
    pid = await _part(client, tenant, loc, 10)
    svc = (await client.post("/api/catalog/services", json={"name": "Fitting", "price": 80}, headers=tenant)).json()["id"]
    bid = await _kit(client, tenant, [
        {"ref_type": "product", "ref_id": pid, "qty": 2},
        {"ref_type": "service", "ref_id": svc, "qty": 1}])
    cid = (await client.post("/api/clients", json={"name": "Owner"}, headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x", "lines": []},
                              headers=tenant)).json()["id"]

    before = (await client.get(f"/api/inventory/parts/{pid}", headers=tenant)).json()["qty_available"]
    # apply the job kit as a WO line (kind=bundle explodes into component lines)
    r = await client.post(f"/api/orders/{oid}/lines", json={"kind": "bundle", "line_type": "part", "description": "Screen combo", "ref_id": bid, "qty": 1}, headers=tenant)
    assert r.status_code in (200, 201), r.text
    after = (await client.get(f"/api/inventory/parts/{pid}", headers=tenant)).json()["qty_available"]
    assert before - after == 2, f"product component should deduct 2 (got {before}->{after})"


@pytest.mark.asyncio
async def test_jobkit_on_estimate_does_not_deduct(client, tenant):
    loc = await _loc(client, tenant)
    pid = await _part(client, tenant, loc, 10)
    bid = await _kit(client, tenant, [{"ref_type": "product", "ref_id": pid, "qty": 2}])
    cid = (await client.post("/api/clients", json={"name": "Owner2"}, headers=tenant)).json()["id"]
    eid = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x",
                                                   "is_estimate": True, "lines": []}, headers=tenant)).json()["id"]
    before = (await client.get(f"/api/inventory/parts/{pid}", headers=tenant)).json()["qty_available"]
    await client.post(f"/api/orders/{eid}/lines", json={"kind": "bundle", "line_type": "part", "description": "Screen combo", "ref_id": bid, "qty": 1}, headers=tenant)
    after = (await client.get(f"/api/inventory/parts/{pid}", headers=tenant)).json()["qty_available"]
    assert before == after, "estimate must not deduct stock"
