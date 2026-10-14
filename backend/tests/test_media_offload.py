"""B3 — media offload. Proves db-legacy unchanged + local backend offloads,
serves, and migrates existing inline media out of the DB."""
import base64

import pytest
from sqlalchemy import select

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"fake-image-bytes" * 20).decode()
DATA_URL = f"data:image/png;base64,{PNG}"


async def _order(client, tenant):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    return (await client.post("/api/orders", json={"location_id": loc, "complaint": "x"}, headers=tenant)).json()["id"]


@pytest.mark.asyncio
async def test_db_backend_keeps_inline(client, tenant, db, monkeypatch):
    """Default backend=db → feed media stays inline, no blob pointer (legacy)."""
    from backend.config import settings
    monkeypatch.setattr(settings, "MEDIA_BACKEND", "db")
    oid = await _order(client, tenant)
    r = await client.post(f"/api/feed/order/{oid}", json={
        "item_type": "photo", "media_data": DATA_URL, "media_name": "p.png", "media_mime": "image/png"}, headers=tenant)
    assert r.status_code in (200, 201)
    feed = (await client.get(f"/api/feed/order/{oid}", headers=tenant)).json()["items"]
    photo = next(i for i in feed if i["item_type"] == "photo")
    assert photo["media_data"] is not None and photo["media_url"] is None


@pytest.mark.asyncio
async def test_local_backend_offloads_and_serves(client, tenant, db, monkeypatch, tmp_path):
    """backend=local → bytes leave the DB, feed exposes a URL, serving returns them."""
    from backend.config import settings
    from backend.models import EventFeedItem
    monkeypatch.setattr(settings, "MEDIA_BACKEND", "local")
    monkeypatch.setattr(settings, "MEDIA_LOCAL_DIR", str(tmp_path))

    oid = await _order(client, tenant)
    await client.post(f"/api/feed/order/{oid}", json={
        "item_type": "photo", "media_data": DATA_URL, "media_name": "p.png", "media_mime": "image/png"}, headers=tenant)

    feed = (await client.get(f"/api/feed/order/{oid}", headers=tenant)).json()["items"]
    photo = next(i for i in feed if i["item_type"] == "photo")
    assert photo["media_url"] is not None            # offloaded → served by URL
    assert photo["media_data"] is None               # not inline anymore

    # the feed row must NOT carry the payload in the DB
    db.expire_all()
    row = (await db.execute(select(EventFeedItem).where(
        EventFeedItem.item_type == "photo"))).scalars().first()
    assert row.media_data is None and row.media_blob_id is not None

    # serving the URL returns the original bytes
    resp = await client.get(photo["media_url"], headers=tenant)
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("image/png")
    assert resp.content == base64.b64decode(PNG)


@pytest.mark.asyncio
async def test_generic_upload_and_status(client, tenant, monkeypatch, tmp_path):
    from backend.config import settings
    monkeypatch.setattr(settings, "MEDIA_BACKEND", "local")
    monkeypatch.setattr(settings, "MEDIA_LOCAL_DIR", str(tmp_path))

    up = await client.post("/api/media", json={"data": DATA_URL, "name": "x.png", "mime": "image/png"}, headers=tenant)
    assert up.status_code == 201
    body = up.json()
    assert body["backend"] == "local" and body["size_bytes"] > 0
    got = await client.get(body["url"], headers=tenant)
    assert got.status_code == 200 and got.content == base64.b64decode(PNG)

    st = (await client.get("/api/media/status", headers=tenant)).json()
    assert st["backend"] == "local" and st["offloaded_blobs"] >= 1


@pytest.mark.asyncio
async def test_migrate_feed_moves_inline_out(client, tenant, db, monkeypatch, tmp_path):
    """An item created under db-legacy is offloaded by the migration endpoint."""
    from backend.config import settings
    from backend.models import EventFeedItem

    # 1) create inline under db
    monkeypatch.setattr(settings, "MEDIA_BACKEND", "db")
    oid = await _order(client, tenant)
    await client.post(f"/api/feed/order/{oid}", json={
        "item_type": "file", "media_data": DATA_URL, "media_name": "d.png", "media_mime": "image/png"}, headers=tenant)

    # 2) switch to local + migrate
    monkeypatch.setattr(settings, "MEDIA_BACKEND", "local")
    monkeypatch.setattr(settings, "MEDIA_LOCAL_DIR", str(tmp_path))
    mig = await client.post("/api/media/migrate-feed", headers=tenant)
    assert mig.status_code == 200 and mig.json()["migrated"] >= 1

    db.expire_all()
    row = (await db.execute(select(EventFeedItem).where(
        EventFeedItem.item_type == "file"))).scalars().first()
    assert row.media_data is None and row.media_blob_id is not None
