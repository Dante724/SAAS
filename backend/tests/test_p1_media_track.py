"""P1 audit 2026-11-12: PERF-01, PERF-02, PERF-22. The public track page's media and the S3 backend.

- PERF-01: the track payload returned every client-visible photo inline as base64, with no limit,
  and the 20 s poll had no ETag. Photos are now URLs served under the track token, polls get 304,
  and photos offloaded to local/S3 storage show (they used to come back as null).
- PERF-02: boto3 ran on the event loop with a new client per call; media had `no-store`.
- PERF-22: feed attachments were capped at 8M base64 *characters* on the db backend and 8 MB of
  *bytes* on the others; recommendations had their own cap. One limit now: MEDIA_MAX_BYTES.
"""
import asyncio
import base64
import threading

import pytest
from sqlalchemy import select

RAW = b"\x89PNG\r\n\x1a\n" + b"A" * 200_000          # a ~200 KB photo
PNG = "data:image/png;base64," + base64.b64encode(RAW).decode()


async def _order(client, tenant):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={"name": "T"}, headers=tenant)).json()["id"]
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"},
                           headers=tenant)).json()
    return o["id"], o["public_token"]


async def _photo(client, tenant, oid, visibility="client", data=PNG, item_type="photo"):
    r = await client.post(f"/api/feed/order/{oid}", headers=tenant, json={
        "item_type": item_type, "media_data": data, "media_name": "p.png", "media_mime": "image/png",
        "visibility": visibility})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_track_returns_media_urls_not_base64(client, tenant):
    oid, tok = await _order(client, tenant)
    for _ in range(3):
        await _photo(client, tenant, oid)
    r = await client.get(f"/api/public/track/{tok}")
    assert r.status_code == 200, r.text
    # Three 200 KB photos used to make this ~800 KB of JSON.
    assert len(r.content) < 30_000, len(r.content)
    photos = [u for u in r.json()["updates"] if u["type"] == "photo"]
    assert len(photos) == 3
    for p in photos:
        assert p["media_data"].startswith(f"/api/media/track/{tok}/feed/")
        assert p["media_url"] == p["media_data"]
        # No staff login: the token alone opens the photo.
        m = await client.get(p["media_data"])
        assert m.status_code == 200
        assert m.content == RAW
        assert m.headers["content-type"].startswith("image/png")
        assert m.headers["x-content-type-options"] == "nosniff"
        assert "max-age" in m.headers["cache-control"] and "private" in m.headers["cache-control"]


async def test_track_media_route_checks_token_order_and_visibility(client, tenant):
    oid, tok = await _order(client, tenant)
    oid2, tok2 = await _order(client, tenant)
    private_id = await _photo(client, tenant, oid, visibility="private")
    public_id = await _photo(client, tenant, oid)
    other_job_id = await _photo(client, tenant, oid2)
    assert (await client.get(f"/api/media/track/{tok}/feed/{public_id}")).status_code == 200
    # an internal photo is never served on the customer link
    assert (await client.get(f"/api/media/track/{tok}/feed/{private_id}")).status_code == 404
    # another job's photo is not reachable with this job's token
    assert (await client.get(f"/api/media/track/{tok}/feed/{other_job_id}")).status_code == 404
    assert (await client.get(f"/api/media/track/{tok2}/feed/{other_job_id}")).status_code == 200
    assert (await client.get(f"/api/media/track/not-a-token/feed/{public_id}")).status_code == 404


async def test_track_media_route_is_tenant_isolated(client, tenant, db):
    """A second shop's token can't open the first shop's photo, even with its id."""
    from backend.models import Location, Order, Organisation
    oid, tok = await _order(client, tenant)
    pid = await _photo(client, tenant, oid)
    other = Organisation(name="Other", plan="free", currency="$", currency_code="USD", timezone="UTC")
    db.add(other)
    await db.flush()
    loc2 = Location(org_id=other.id, name="L2")
    db.add(loc2)
    await db.flush()
    o2 = Order(org_id=other.id, order_number="X-1", status="new", location_id=loc2.id)
    db.add(o2)
    await db.commit()
    assert (await client.get(f"/api/media/track/{o2.public_token}/feed/{pid}")).status_code == 404


async def test_track_poll_etag_returns_304_until_something_changes(client, tenant):
    oid, tok = await _order(client, tenant)
    await _photo(client, tenant, oid)
    r1 = await client.get(f"/api/public/track/{tok}")
    etag = r1.headers.get("etag")
    assert etag
    assert "public" not in r1.headers.get("cache-control", "")
    r2 = await client.get(f"/api/public/track/{tok}", headers={"If-None-Match": etag})
    assert r2.status_code == 304
    assert r2.content == b""
    await client.post(f"/api/feed/order/{oid}", headers=tenant,
                      json={"item_type": "comment", "body": "Ready soon", "visibility": "client"})
    r3 = await client.get(f"/api/public/track/{tok}", headers={"If-None-Match": etag})
    assert r3.status_code == 200
    assert r3.headers["etag"] != etag


async def test_offloaded_photo_shows_on_track_page(client, tenant, monkeypatch, tmp_path):
    """MEDIA_BACKEND=local/s3 keeps no inline copy; the track page used to send null for it."""
    from backend.config import settings
    monkeypatch.setattr(settings, "MEDIA_BACKEND", "local")
    monkeypatch.setattr(settings, "MEDIA_LOCAL_DIR", str(tmp_path))
    oid, tok = await _order(client, tenant)
    await _photo(client, tenant, oid)
    photo = next(u for u in (await client.get(f"/api/public/track/{tok}")).json()["updates"]
                 if u["type"] == "photo")
    assert photo["media_data"]
    m = await client.get(photo["media_data"])
    assert m.status_code == 200 and m.content == RAW


async def test_portal_job_view_uses_portal_scoped_urls(client, tenant, db):
    from backend.models import Client, Order
    from backend.services import portal_links
    oid, _tok = await _order(client, tenant)
    await _photo(client, tenant, oid)
    order = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    cl = (await db.execute(select(Client).where(Client.id == order.client_id))).scalar_one()
    ptoken = await portal_links.issue(db, cl)
    await db.commit()
    r = await client.get(f"/api/public/portal/{ptoken}/order/{oid}")
    assert r.status_code == 200, r.text
    photo = next(u for u in r.json()["updates"] if u["type"] == "photo")
    assert photo["media_data"].startswith("/api/media/")
    m = await client.get(photo["media_data"])
    assert m.status_code == 200 and m.content == RAW


async def test_track_recommendation_photo_is_a_url(client, tenant, db):
    from backend.models import Organisation
    oid, tok = await _order(client, tenant)
    org = (await db.execute(select(Organisation))).scalars().first()
    org.oe_allow_recommend = True
    await db.commit()
    r = await client.post(f"/api/orders/{oid}/recommendations", headers=tenant,
                          json={"title": "Brakes", "kind": "custom", "unit_price": 10, "media_data": PNG})
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    sent = await client.post(f"/api/orders/{oid}/recommendations/{rid}/publish", headers=tenant)
    assert sent.status_code < 300, sent.text
    recs = (await client.get(f"/api/public/track/{tok}")).json()["recommendations"]
    assert recs
    url = recs[0]["media_data"]
    assert url.startswith(f"/api/media/track/{tok}/rec/")
    m = await client.get(url)
    assert m.status_code == 200 and m.content == RAW


async def test_feed_and_recommendation_use_the_same_byte_limit(client, tenant, monkeypatch):
    """PERF-22: on the default db backend the feed checked characters, not MEDIA_MAX_BYTES."""
    from backend.config import settings
    monkeypatch.setattr(settings, "MEDIA_BACKEND", "db")
    monkeypatch.setattr(settings, "MEDIA_MAX_BYTES", 100_000)
    oid, _ = await _order(client, tenant)
    r = await client.post(f"/api/feed/order/{oid}", headers=tenant, json={
        "item_type": "photo", "media_data": PNG, "visibility": "client"})
    assert r.status_code == 413, r.text
    r = await client.post(f"/api/orders/{oid}/recommendations", headers=tenant,
                          json={"title": "Brakes", "kind": "custom", "media_data": PNG})
    assert r.status_code == 413, r.text
    small = "data:image/png;base64," + base64.b64encode(RAW[:50_000]).decode()
    r = await client.post(f"/api/feed/order/{oid}", headers=tenant, json={
        "item_type": "photo", "media_data": small, "visibility": "client"})
    assert r.status_code == 201, r.text


class _FakeS3:
    instances = 0

    def __init__(self):
        type(self).instances += 1
        self.objects, self.threads = {}, []

    def put_object(self, Bucket, Key, Body, ContentType):
        self.threads.append(threading.get_ident())
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):
        self.threads.append(threading.get_ident())
        import io
        return {"Body": io.BytesIO(self.objects[Key])}

    def delete_object(self, Bucket, Key):
        self.threads.append(threading.get_ident())
        self.objects.pop(Key, None)


async def test_s3_calls_run_off_the_event_loop_with_one_client(client, tenant, monkeypatch):
    """PERF-02: every boto3 call used to block the event loop and built a new client."""
    import sys
    import types

    from backend.config import settings
    from backend.services import media
    fake = _FakeS3()
    _FakeS3.instances = 1
    made = []
    mod = types.SimpleNamespace(client=lambda *a, **k: made.append(1) or fake)
    monkeypatch.setitem(sys.modules, "boto3", mod)
    monkeypatch.setattr(settings, "MEDIA_BACKEND", "s3")
    monkeypatch.setattr(settings, "MEDIA_S3_BUCKET", "bucket")
    media._s3_client.cache_clear()
    loop_thread = threading.get_ident()

    oid, tok = await _order(client, tenant)
    await _photo(client, tenant, oid)
    await _photo(client, tenant, oid, data="data:image/png;base64," + base64.b64encode(RAW + b"x").decode())
    for u in (await client.get(f"/api/public/track/{tok}")).json()["updates"]:
        if u["type"] == "photo":
            m = await client.get(u["media_data"])
            assert m.status_code == 200
    assert fake.threads, "S3 was never called"
    assert loop_thread not in fake.threads
    assert len(made) == 1, "a new boto3 client per call"
    media._s3_client.cache_clear()
    await asyncio.sleep(0)


async def test_authenticated_media_is_privately_cacheable(client, tenant, monkeypatch, tmp_path):
    from backend.config import settings
    monkeypatch.setattr(settings, "MEDIA_BACKEND", "local")
    monkeypatch.setattr(settings, "MEDIA_LOCAL_DIR", str(tmp_path))
    r = await client.post("/api/media", headers=tenant, json={"data": PNG, "name": "p.png"})
    m = await client.get(r.json()["url"], headers=tenant)
    assert m.status_code == 200
    cc = m.headers["cache-control"]
    assert "private" in cc and "max-age" in cc and "no-store" not in cc
