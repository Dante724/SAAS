"""Velocity forecast → one-click draft POs, grouped by default supplier."""


async def _loc(client, tenant):
    return (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]


async def test_forecast_to_draft_po(client, tenant):
    loc = await _loc(client, tenant)
    sup = (await client.post("/api/suppliers", json={"name": "Acme Parts", "lead_days": 3},
           headers=tenant)).json()
    sup_id = sup.get("id") or sup.get("supplier", {}).get("id")
    p = (await client.post("/api/catalog/products", json={
        "name": "Fast mover", "cost_price": 5, "sell_price": 12,
        "default_supplier_id": sup_id}, headers=tenant)).json()
    # stock 10, then consume 8 via an order line -> velocity + low free
    await client.post(f"/api/inventory/parts/{p['id']}/adjust",
        json={"location_id": loc, "movement_type": "receive", "qty": 10}, headers=tenant)
    o = (await client.post("/api/orders", json={"location_id": loc, "complaint": "x"}, headers=tenant)).json()
    r = await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "part", "kind": "product", "ref_id": p["id"], "description": "Fast mover",
        "unit_price": 12, "qty": 8}, headers=tenant)
    assert r.status_code == 201, r.text

    fc = (await client.get("/api/inventory/reorder-forecast", headers=tenant)).json()
    ids = [i["part_id"] for i in fc["items"]]
    assert p["id"] in ids, fc

    made = await client.post("/api/inventory/reorder-forecast/create-pos", json={}, headers=tenant)
    assert made.status_code == 201, made.text
    body = made.json()
    assert body["po_count"] >= 1 and not body["skipped_no_supplier"], body

    pos = (await client.get("/api/inventory/po", headers=tenant)).json()["items"]
    assert any(po.get("status") == "draft" for po in pos), pos


async def test_part_without_supplier_is_skipped(client, tenant):
    loc = await _loc(client, tenant)
    p = (await client.post("/api/catalog/products", json={
        "name": "Orphan mover", "cost_price": 5, "sell_price": 12}, headers=tenant)).json()
    await client.post(f"/api/inventory/parts/{p['id']}/adjust",
        json={"location_id": loc, "movement_type": "receive", "qty": 10}, headers=tenant)
    o = (await client.post("/api/orders", json={"location_id": loc, "complaint": "x"}, headers=tenant)).json()
    await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "part", "kind": "product", "ref_id": p["id"], "description": "Orphan mover",
        "unit_price": 12, "qty": 8}, headers=tenant)
    made = (await client.post("/api/inventory/reorder-forecast/create-pos", json={}, headers=tenant)).json()
    assert "Orphan mover" in made["skipped_no_supplier"], made


async def test_reorder_digest_notifies(client, tenant):
    loc = await _loc(client, tenant)
    p = (await client.post("/api/catalog/products", json={
        "name": "Digest mover", "cost_price": 5, "sell_price": 12}, headers=tenant)).json()
    await client.post(f"/api/inventory/parts/{p['id']}/adjust",
        json={"location_id": loc, "movement_type": "receive", "qty": 10}, headers=tenant)
    o = (await client.post("/api/orders", json={"location_id": loc, "complaint": "x"}, headers=tenant)).json()
    await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "part", "kind": "product", "ref_id": p["id"], "description": "Digest mover",
        "unit_price": 12, "qty": 8}, headers=tenant)
    d = await client.post("/api/inventory/reorder-digest", json={}, headers=tenant)
    assert d.status_code == 200, d.text
    assert d.json()["sent"] >= 1 and d.json()["items"] >= 1, d.json()
    # it lands in the staff notification feed
    feed = (await client.get("/api/tech/notifications", headers=tenant)).json()
    assert any(n["kind"] == "inventory.reorder" for n in feed["items"]), feed
