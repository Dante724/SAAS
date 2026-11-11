"""Hardening council M16: "who changed the price on this job?"

Adding a line, editing its qty/price/discount and removing it left no AuditLog row — only
order.create, order.status_change and a few others were audited. Each line change now writes
order.line_add / order.line_update / order.line_delete against the order, in the same transaction.
"""
import pytest

pytestmark = pytest.mark.asyncio


async def _order(client, h):
    cid = (await client.post("/api/clients", json={"name": "Audited Line Co", "phone": "+1"}, headers=h)).json()["id"]
    r = await client.post("/api/orders", json={"client_id": cid}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _audit(client, h, oid):
    r = await client.get(f"/api/compliance/audit?entity_type=order&entity_id={oid}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()["items"]


async def test_add_edit_delete_line_are_audited(client, tenant):
    oid = await _order(client, tenant)
    r = await client.post(f"/api/orders/{oid}/lines", json={
        "line_type": "labour", "description": "Fix", "qty": 1, "unit_price": 100}, headers=tenant)
    assert r.status_code == 201, r.text
    lid = r.json()["line_id"]

    added = [a for a in await _audit(client, tenant, oid) if a["action"] == "order.line_add"]
    assert len(added) == 1, [a["action"] for a in await _audit(client, tenant, oid)]
    assert added[0]["diff"]["line_id"] == lid
    assert added[0]["diff"]["description"] == "Fix"
    assert float(added[0]["diff"]["unit_price"]) == 100

    r = await client.patch(f"/api/orders/{oid}/lines/{lid}", json={"unit_price": 10, "discount": 50}, headers=tenant)
    assert r.status_code == 200, r.text
    edited = [a for a in await _audit(client, tenant, oid) if a["action"] == "order.line_update"]
    assert len(edited) == 1, [a["action"] for a in await _audit(client, tenant, oid)]
    diff = edited[0]["diff"]
    assert diff["line_id"] == lid
    assert [float(x) for x in diff["unit_price"]] == [100, 10]
    assert [float(x or 0) for x in diff["discount"]] == [0, 50]
    assert "qty" not in diff            # unchanged fields aren't listed

    r = await client.delete(f"/api/orders/{oid}/lines/{lid}", headers=tenant)
    assert r.status_code == 200, r.text
    deleted = [a for a in await _audit(client, tenant, oid) if a["action"] == "order.line_delete"]
    assert len(deleted) == 1, [a["action"] for a in await _audit(client, tenant, oid)]
    assert deleted[0]["diff"]["line_id"] == lid
    assert deleted[0]["diff"]["description"] == "Fix"
    assert float(deleted[0]["diff"]["unit_price"]) == 10


async def test_no_op_line_edit_writes_no_audit_row(client, tenant):
    oid = await _order(client, tenant)
    r = await client.post(f"/api/orders/{oid}/lines", json={
        "line_type": "labour", "description": "Fix", "qty": 1, "unit_price": 100}, headers=tenant)
    lid = r.json()["line_id"]
    r = await client.patch(f"/api/orders/{oid}/lines/{lid}", json={"unit_price": 100}, headers=tenant)
    assert r.status_code == 200, r.text
    assert not [a for a in await _audit(client, tenant, oid) if a["action"] == "order.line_update"]


async def test_merged_add_of_the_same_product_is_audited_as_an_edit(client, tenant):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    p = (await client.post("/api/inventory/parts", json={
        "name": "Screen", "sku": "SCR-M16", "cost_price": 5, "sell_price": 10}, headers=tenant)).json()
    r = await client.post(f"/api/inventory/parts/{p['id']}/adjust", json={
        "location_id": loc, "qty": 5, "movement_type": "receive"}, headers=tenant)
    assert r.status_code in (200, 201), r.text
    oid = await _order(client, tenant)
    body = {"description": "Screen", "kind": "product", "line_type": "part", "ref_id": p["id"], "qty": 1, "unit_price": 10}
    a = await client.post(f"/api/orders/{oid}/lines", json=body, headers=tenant)
    b = await client.post(f"/api/orders/{oid}/lines", json=body, headers=tenant)
    assert a.status_code == 201 and b.status_code == 201, (a.text, b.text)
    assert b.json().get("merged") is True
    rows = await _audit(client, tenant, oid)
    assert len([x for x in rows if x["action"] == "order.line_add"]) == 1
    merged = [x for x in rows if x["action"] == "order.line_update"]
    assert len(merged) == 1, [x["action"] for x in rows]
    assert [float(x) for x in merged[0]["diff"]["qty"]] == [1, 2]
