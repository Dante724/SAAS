"""A4 — posting freeze: a posting whose stock has moved downstream can't be deleted."""
import pytest


async def _product(client, h, name):
    r = await client.post("/api/inventory/parts", json={"name": name, "uom": "pcs"}, headers=h)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _warehouse_id(client, h):
    r = await client.get("/api/settings/warehouses", headers=h)
    body = r.json() if r.status_code == 200 else {}
    items = body.get("items", body) if isinstance(body, dict) else body
    if items:
        return items[0]["id"]
    r = await client.post("/api/settings/warehouses", json={"name": "Main", "kind": "product"}, headers=h)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_posting_delete_blocked_after_downstream_movement(client, tenant):
    h = tenant
    pid = await _product(client, h, "Widget")
    wid = await _warehouse_id(client, h)

    # post 10 in
    r = await client.post("/api/stock-docs/postings", json={
        "warehouse_id": wid, "settlement": "cash",
        "lines": [{"part_id": pid, "qty": 10, "unit_cost": 5}]}, headers=h)
    assert r.status_code in (200, 201), r.text
    posting_id = r.json()["id"]

    # write off 4 (downstream consumption)
    r = await client.post("/api/stock-docs/write-offs", json={
        "warehouse_id": wid, "reason": "damage",
        "lines": [{"part_id": pid, "qty": 4}]}, headers=h)
    assert r.status_code in (200, 201), r.text

    # now the posting can't be deleted — only 6 of 10 remain
    r = await client.delete(f"/api/stock-docs/postings/{posting_id}", headers=h)
    assert r.status_code == 409, r.text
    assert "used" in r.text.lower() or "left on hand" in r.text.lower()


@pytest.mark.asyncio
async def test_intact_posting_can_be_deleted(client, tenant):
    h = tenant
    pid = await _product(client, h, "Gadget")
    wid = await _warehouse_id(client, h)
    r = await client.post("/api/stock-docs/postings", json={
        "warehouse_id": wid, "settlement": "cash",
        "lines": [{"part_id": pid, "qty": 3, "unit_cost": 2}]}, headers=h)
    posting_id = r.json()["id"]
    # nothing consumed → deletable
    r = await client.delete(f"/api/stock-docs/postings/{posting_id}", headers=h)
    assert r.status_code == 200, r.text
