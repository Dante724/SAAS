"""Build 144ai — an asset photo set via the asset-update endpoint must surface in
the work-order detail, the work-order list, and the kanban board card."""
import pytest

# a tiny 1x1 gif data URL — stands in for a real photo
IMG = "data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw=="


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


@pytest.mark.asyncio
async def test_asset_image_roundtrips_to_order_and_board(client, tenant):
    loc = await _loc(client, tenant)
    cid = (await client.post("/api/clients", json={"name": "Pic", "phone": "+15551111"},
                             headers=tenant)).json()["id"]
    o = await client.post("/api/orders", json={
        "client_id": cid, "location_id": loc, "complaint": "cracked",
        "asset_brand": "Apple", "asset_model": "iPhone 15"}, headers=tenant)
    assert o.status_code == 201, o.text
    order = o.json()
    device_id = order["device"]["id"]
    # no photo yet
    assert order["device"].get("image_data") in (None, "")

    # attach the photo through the asset-update endpoint
    up = await client.put(f"/api/assets/{device_id}", json={"image_data": IMG}, headers=tenant)
    assert up.status_code == 200, up.text
    assert up.json()["image_data"] == IMG

    # order detail carries it
    detail = (await client.get(f"/api/orders/{order['id']}", headers=tenant)).json()
    assert detail["device"]["image_data"] == IMG

    # work-order list carries it
    lst = (await client.get("/api/orders?is_estimate=false", headers=tenant)).json()
    row = next(r for r in lst["items"] if r["id"] == order["id"])
    assert row["device"]["image_data"] == IMG

    # board card exposes it as device_image
    board = (await client.get("/api/orders/board?is_estimate=false", headers=tenant)).json()
    cards = [c for col in board["columns"] for c in col["cards"]]
    card = next(c for c in cards if c["id"] == order["id"])
    assert card["device_image"] == IMG


@pytest.mark.asyncio
async def test_reschedule_endpoint_sets_and_clears(client, tenant):
    loc = await _loc(client, tenant)
    cid = (await client.post("/api/clients", json={"name": "Sked"}, headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", json={
        "client_id": cid, "location_id": loc, "complaint": "tune"}, headers=tenant)).json()["id"]

    r = await client.patch(f"/api/orders/{oid}/schedule",
                           json={"scheduled_for": "2026-09-01T10:00", "scheduled_to": "2026-09-01T11:00"},
                           headers=tenant)
    assert r.status_code == 200, r.text
    d = (await client.get(f"/api/orders/{oid}", headers=tenant)).json()
    assert d["scheduled_for"].startswith("2026-09-01T10:00")

    # clearing
    r2 = await client.patch(f"/api/orders/{oid}/schedule",
                            json={"scheduled_for": None, "scheduled_to": None}, headers=tenant)
    assert r2.status_code == 200, r2.text
    d2 = (await client.get(f"/api/orders/{oid}", headers=tenant)).json()
    assert d2["scheduled_for"] in (None, "")
