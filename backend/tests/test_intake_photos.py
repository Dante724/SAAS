"""Build 144ak — intake (condition) photos on a work order: uploaded via the media endpoint,
attached at creation, surfaced as URLs in the order detail, and editable afterwards."""
import base64
import pytest

_PX = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 32).decode()


async def _upload(client, tenant):
    r = await client.post("/api/media", json={"data": _PX, "name": "intake.png", "mime": "image/png"}, headers=tenant)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_intake_photos_attach_on_create_and_surface_as_urls(client, tenant):
    cid = (await client.post("/api/clients", json={"name": "Snap Co"}, headers=tenant)).json()["id"]
    b1 = await _upload(client, tenant)
    b2 = await _upload(client, tenant)

    oid = (await client.post("/api/orders", json={
        "client_id": cid, "complaint": "cracked screen", "intake_photos": [b1, b2]}, headers=tenant)).json()["id"]

    detail = (await client.get(f"/api/orders/{oid}", headers=tenant)).json()
    photos = detail["intake_photos"]
    assert [p["id"] for p in photos] == [b1, b2], detail
    assert photos[0]["url"] == f"/api/media/{b1}"


@pytest.mark.asyncio
async def test_intake_photos_patch_replaces_set(client, tenant):
    cid = (await client.post("/api/clients", json={"name": "Snap Co"}, headers=tenant)).json()["id"]
    b1 = await _upload(client, tenant)
    oid = (await client.post("/api/orders", json={"client_id": cid, "complaint": "x"}, headers=tenant)).json()["id"]
    assert (await client.get(f"/api/orders/{oid}", headers=tenant)).json()["intake_photos"] == []

    r = await client.patch(f"/api/orders/{oid}/intake-photos", json={"photos": [b1]}, headers=tenant)
    assert r.status_code == 200, r.text
    assert [p["id"] for p in (await client.get(f"/api/orders/{oid}", headers=tenant)).json()["intake_photos"]] == [b1]

    # Empty list clears them.
    await client.patch(f"/api/orders/{oid}/intake-photos", json={"photos": []}, headers=tenant)
    assert (await client.get(f"/api/orders/{oid}", headers=tenant)).json()["intake_photos"] == []
