"""144dg — marking up an intake photo keeps the untouched original (dispute protection).
The markup is a linked derivative, not a replacement."""
import pytest

PX = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _media(client, h):
    return (await client.post("/api/media", json={"data": PX, "name": "p.png", "mime": "image/png"}, headers=h)).json()["id"]


@pytest.mark.asyncio
async def test_markup_keeps_original_and_links_marked(client, tenant):
    loc = await _loc(client, tenant)
    cid = (await client.post("/api/clients", json={"name": "C"}, headers=tenant)).json()["id"]
    orig = await _media(client, tenant)
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x",
                                                 "intake_photos": [orig]}, headers=tenant)).json()

    marked = await _media(client, tenant)
    r = await client.patch(f"/api/orders/{o['id']}/intake-markups",
                           json={"original_id": orig, "marked_id": marked}, headers=tenant)
    assert r.status_code == 200, r.text

    d = (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()
    photos = d["intake_photos"]
    assert len(photos) == 1
    p = photos[0]
    assert p["id"] == orig, "the original id is preserved, not replaced"
    assert p["marked_id"] == marked and p["marked_url"].endswith(marked)


@pytest.mark.asyncio
async def test_markup_rejects_foreign_and_unknown(client, tenant):
    loc = await _loc(client, tenant)
    cid = (await client.post("/api/clients", json={"name": "C"}, headers=tenant)).json()["id"]
    orig = await _media(client, tenant)
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x",
                                                 "intake_photos": [orig]}, headers=tenant)).json()
    # original not on the order
    r1 = await client.patch(f"/api/orders/{o['id']}/intake-markups",
                            json={"original_id": "nope", "marked_id": await _media(client, tenant)}, headers=tenant)
    assert r1.status_code == 400
    # marked blob doesn't exist
    r2 = await client.patch(f"/api/orders/{o['id']}/intake-markups",
                            json={"original_id": orig, "marked_id": "ghost"}, headers=tenant)
    assert r2.status_code == 400


@pytest.mark.asyncio
async def test_removing_photo_drops_its_markup(client, tenant):
    loc = await _loc(client, tenant)
    cid = (await client.post("/api/clients", json={"name": "C"}, headers=tenant)).json()["id"]
    orig = await _media(client, tenant)
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x",
                                                 "intake_photos": [orig]}, headers=tenant)).json()
    marked = await _media(client, tenant)
    await client.patch(f"/api/orders/{o['id']}/intake-markups", json={"original_id": orig, "marked_id": marked}, headers=tenant)
    # remove the original photo → its markup mapping is pruned (no orphan)
    await client.patch(f"/api/orders/{o['id']}/intake-photos", json={"photos": []}, headers=tenant)
    d = (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()
    assert d["intake_photos"] == []
