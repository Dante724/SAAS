"""144-G: archived work orders — single restore + bulk archive/restore."""
import pytest


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _order(client, h, loc):
    cid = (await client.post("/api/clients", json={"name": "C"}, headers=h)).json()["id"]
    return (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"},
                              headers=h)).json()


@pytest.mark.asyncio
async def test_archive_then_restore_roundtrip(client, tenant):
    loc = await _loc(client, tenant)
    o = await _order(client, tenant, loc)
    # archive (no payments → no refund needed)
    r = await client.post(f"/api/orders/{o['id']}/archive", json={"reason": "dupe", "refund": False}, headers=tenant)
    assert r.status_code == 200 and r.json()["archived"] is True
    # hidden from the normal list, present in /archived
    lst = (await client.get("/api/orders", headers=tenant)).json()
    assert not any(x["id"] == o["id"] for x in lst["items"])
    arch = (await client.get("/api/orders/archived", headers=tenant)).json()
    assert any(x["id"] == o["id"] for x in arch["items"])
    # restore → back in the active list
    r2 = await client.post(f"/api/orders/{o['id']}/restore", headers=tenant)
    assert r2.status_code == 200 and r2.json()["archived"] is False
    lst2 = (await client.get("/api/orders", headers=tenant)).json()
    assert any(x["id"] == o["id"] for x in lst2["items"])


@pytest.mark.asyncio
async def test_restore_rejects_non_archived(client, tenant):
    loc = await _loc(client, tenant)
    o = await _order(client, tenant, loc)
    r = await client.post(f"/api/orders/{o['id']}/restore", headers=tenant)
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_bulk_archive_and_restore(client, tenant):
    loc = await _loc(client, tenant)
    o1 = await _order(client, tenant, loc)
    o2 = await _order(client, tenant, loc)
    r = await client.post("/api/orders/bulk", json={
        "order_ids": [o1["id"], o2["id"]], "action": "archive", "reason": "cleanup", "refund": False}, headers=tenant)
    assert r.status_code == 200 and r.json()["updated"] == 2
    arch = (await client.get("/api/orders/archived", headers=tenant)).json()
    ids = {x["id"] for x in arch["items"]}
    assert o1["id"] in ids and o2["id"] in ids
    # bulk restore both
    r2 = await client.post("/api/orders/bulk", json={
        "order_ids": [o1["id"], o2["id"]], "action": "restore"}, headers=tenant)
    assert r2.status_code == 200 and r2.json()["updated"] == 2
    lst = (await client.get("/api/orders", headers=tenant)).json()
    live = {x["id"] for x in lst["items"]}
    assert o1["id"] in live and o2["id"] in live


@pytest.mark.asyncio
async def test_bulk_archive_skips_order_with_unrefunded_payment(client, tenant, db):
    """An order carrying money can't be archived without refunding — bulk skips it, not 500s."""
    from backend.models import Payment
    loc = await _loc(client, tenant)
    o1 = await _order(client, tenant, loc)      # clean
    o2 = await _order(client, tenant, loc)      # has a payment
    db.add(Payment(order_id=o2["id"], method="cash", kind="payment", amount=25))
    await db.commit()
    r = await client.post("/api/orders/bulk", json={
        "order_ids": [o1["id"], o2["id"]], "action": "archive", "reason": "x", "refund": False}, headers=tenant)
    assert r.status_code == 200
    body = r.json()
    assert body["updated"] == 1
    assert len(body["skipped"]) == 1 and body["skipped"][0]["id"] == o2["id"]
