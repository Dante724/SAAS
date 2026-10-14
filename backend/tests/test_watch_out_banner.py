"""Build 144ag — per-contact and per-asset "watch-out" warnings surface on a work order.

A watch-out is a prominent handling warning the front desk should see before touching a
job ("battery swollen", "customer disputes charges"). It's stored on the contact and on the
asset, and the order-detail payload carries both so the WO drawer can banner them.
"""
import pytest


@pytest.mark.asyncio
async def test_watch_out_on_client_and_device_shows_on_order(client, tenant):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]

    cid = (await client.post("/api/clients", json={"name": "Risky Co"}, headers=tenant)).json()["id"]
    # Set the contact-level watch-out.
    r = await client.patch(f"/api/clients/{cid}", json={"watch_out": "Always disputes charges"}, headers=tenant)
    assert r.status_code == 200, r.text
    assert (await client.get(f"/api/clients/{cid}", headers=tenant)).json()["watch_out"] == "Always disputes charges"

    # Create an asset for the client and set the asset-level watch-out.
    aid = (await client.post("/api/assets", json={
        "owner_type": "client", "client_id": cid, "brand": "Acme", "model": "X1"}, headers=tenant)).json()["id"]
    r = await client.put(f"/api/assets/{aid}", json={"watch_out": "Battery swollen — handle with care"}, headers=tenant)
    assert r.status_code == 200, r.text
    assert (await client.get(f"/api/assets/{aid}", headers=tenant)).json()["watch_out"] == "Battery swollen — handle with care"

    # A work order for that client + asset carries both watch-outs in its detail payload.
    oid = (await client.post("/api/orders", json={
        "client_id": cid, "device_id": aid, "location_id": loc, "complaint": "won't boot"}, headers=tenant)).json()["id"]
    detail = (await client.get(f"/api/orders/{oid}", headers=tenant)).json()
    assert detail["client"]["watch_out"] == "Always disputes charges", detail["client"]
    assert detail["device"]["watch_out"] == "Battery swollen — handle with care", detail["device"]


@pytest.mark.asyncio
async def test_watch_out_absent_is_null(client, tenant):
    cid = (await client.post("/api/clients", json={"name": "Clean Co"}, headers=tenant)).json()["id"]
    assert (await client.get(f"/api/clients/{cid}", headers=tenant)).json()["watch_out"] is None
