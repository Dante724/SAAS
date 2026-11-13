"""Wave 4, group F2 (infra/perf): PERF-05 / PERF-06 for the staff feed and recommendations.

- PERF-05: the staff work-order timeline (`GET /api/feed/...`) returned every attachment inline as
  base64 (up to 200 items x 8 MB), and the recommendations list did the same for its photos.
  Both now send a `media_url` the staff app loads with its session; the bytes are never selected
  by the list query.
- PERF-06: new feed attachments and recommendation photos are written through `media.store`
  whatever MEDIA_BACKEND is, so the bytes live in `media_blobs` (and on local/S3 storage when that
  backend is on), not in the hot tables. `media.backfill` moves existing inline rows, a batch at
  a time, and is safe to run again.
"""
import base64

import pytest
from sqlalchemy import event, func, select

RAW = b"\x89PNG\r\n\x1a\n" + b"B" * 50_000
PNG = "data:image/png;base64," + base64.b64encode(RAW).decode()

pytestmark = pytest.mark.asyncio


class _Sql:
    def __init__(self, engine):
        self.engine, self.sql = engine, []

    def _hit(self, conn, cur, stmt, params, ctx, many):
        self.sql.append(stmt)

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._hit)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._hit)


def _selected(sql: list[str], column: str) -> list[str]:
    """Statements that SELECT `column` as a value (an IS NOT NULL test or substr() is fine)."""
    import re
    bad = []
    for s in sql:
        for m in re.finditer(r"\bSELECT\b(.*?)\bFROM\b", s, re.S | re.I):
            cols = re.sub(r"(length|substr|substring)\s*\([^()]*\)", "", m.group(1), flags=re.I)
            cols = re.sub(r"[\w.]+\s+IS\s+(NOT\s+)?NULL", "", cols, flags=re.I)
            if column in cols:
                bad.append(s[:200])
    return bad


async def _order(client, tenant):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={"name": "T"}, headers=tenant)).json()["id"]
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"},
                           headers=tenant)).json()
    return o["id"], o["public_token"], cid


async def _org_id(client, tenant):
    return (await client.get("/api/auth/me", headers=tenant)).json()["org_id"]


async def _other_shop(client):
    import uuid
    r = await client.post("/api/auth/register", json={
        "org_name": "Other", "name": "O", "email": f"w4o-{uuid.uuid4().hex[:8]}@test.dev",
        "password": "pw-test-12345"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ── staff feed ─────────────────────────────────────────────────────────────────────────────
async def test_feed_upload_goes_through_the_media_store_on_the_db_backend(client, tenant, db, monkeypatch):
    from backend.config import settings
    from backend.models import EventFeedItem, MediaBlob
    monkeypatch.setattr(settings, "MEDIA_BACKEND", "db")
    oid, _, _ = await _order(client, tenant)
    r = await client.post(f"/api/feed/order/{oid}", headers=tenant, json={
        "item_type": "photo", "media_data": PNG, "media_name": "p.png", "media_mime": "image/png"})
    assert r.status_code == 201, r.text
    row = (await db.execute(select(EventFeedItem).where(EventFeedItem.id == r.json()["id"]))).scalar_one()
    assert row.media_data is None and row.media_blob_id
    blob = await db.get(MediaBlob, row.media_blob_id)
    assert blob.backend == "db" and blob.size_bytes == len(RAW)
    assert r.json()["media_data"] is None and r.json()["media_url"] == f"/api/media/{row.media_blob_id}"


async def test_feed_list_sends_urls_and_never_selects_the_bytes(client, tenant, db):
    from backend.models import EventFeedItem
    oid, _, _ = await _order(client, tenant)
    for _ in range(3):
        await client.post(f"/api/feed/order/{oid}", headers=tenant, json={
            "item_type": "photo", "media_data": PNG, "media_name": "p.png", "media_mime": "image/png"})
    # one legacy row, written inline before this change
    org = await _org_id(client, tenant)
    legacy = EventFeedItem(org_id=org, entity_type="order", entity_id=oid, item_type="file",
                           media_data=PNG, media_name="old.png", media_mime="image/png")
    db.add(legacy)
    await db.commit()
    with _Sql(db.bind.sync_engine) as c:
        r = await client.get(f"/api/feed/order/{oid}", headers=tenant)
    assert r.status_code == 200
    assert not _selected(c.sql, "media_data"), _selected(c.sql, "media_data")
    items = [i for i in r.json()["items"] if i["item_type"] in ("photo", "file")]
    assert len(items) == 4
    assert len(r.content) < 10_000, len(r.content)        # ~270 KB of base64 before
    for i in items:
        assert i["media_data"] is None and i["media_url"]
        m = await client.get(i["media_url"], headers=tenant)
        assert m.status_code == 200 and m.content == RAW
    old = next(i for i in items if i["id"] == legacy.id)
    assert old["media_url"] == f"/api/feed/item/{legacy.id}/media"


async def test_legacy_feed_media_route_is_scoped_to_the_shop(client, tenant, db):
    from backend.models import EventFeedItem
    oid, _, _ = await _order(client, tenant)
    org = await _org_id(client, tenant)
    legacy = EventFeedItem(org_id=org, entity_type="order", entity_id=oid, item_type="photo",
                           media_data=PNG, media_mime="image/png")
    db.add(legacy)
    await db.commit()
    other = await _other_shop(client)
    assert (await client.get(f"/api/feed/item/{legacy.id}/media", headers=other)).status_code == 404
    assert (await client.get(f"/api/feed/item/{legacy.id}/media")).status_code == 401


# ── recommendations ────────────────────────────────────────────────────────────────────────
async def test_recommendation_photo_is_stored_as_a_blob_and_listed_as_a_url(client, tenant, db):
    from backend.models import MediaBlob, Recommendation
    from backend.services import media
    oid, _, _ = await _order(client, tenant)
    r = await client.post(f"/api/orders/{oid}/recommendations", headers=tenant,
                          json={"title": "Brakes", "kind": "custom", "unit_price": 10, "media_data": PNG})
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    row = (await db.execute(select(Recommendation).where(Recommendation.id == rid))).scalar_one()
    blob_id = media.ref_id(row.media_data)
    assert blob_id and (await db.get(MediaBlob, blob_id)).size_bytes == len(RAW)
    with _Sql(db.bind.sync_engine) as c:
        lst = await client.get(f"/api/orders/{oid}/recommendations", headers=tenant)
    assert lst.status_code == 200
    assert not _selected(c.sql, "media_data"), _selected(c.sql, "media_data")
    item = lst.json()["items"][0]
    assert item["media_data"] is None
    assert item["media_url"] == f"/api/orders/{oid}/recommendations/{rid}/media"
    m = await client.get(item["media_url"], headers=tenant)
    assert m.status_code == 200 and m.content == RAW and m.headers["content-type"].startswith("image/png")
    other = await _other_shop(client)
    assert (await client.get(item["media_url"], headers=other)).status_code in (403, 404)


async def test_legacy_inline_recommendation_photo_still_shows(client, tenant, db):
    from backend.models import Recommendation
    oid, _, _ = await _order(client, tenant)
    org = await _org_id(client, tenant)
    rec = Recommendation(org_id=org, order_id=oid, title="Old", kind="custom", media_data=PNG)
    db.add(rec)
    await db.commit()
    item = (await client.get(f"/api/orders/{oid}/recommendations", headers=tenant)).json()["items"][0]
    m = await client.get(item["media_url"], headers=tenant)
    assert m.status_code == 200 and m.content == RAW


async def test_track_page_still_serves_a_stored_recommendation_photo(client, tenant, db):
    from backend.models import Organisation
    oid, tok, _ = await _order(client, tenant)
    org = (await db.execute(select(Organisation).where(
        Organisation.id == await _org_id(client, tenant)))).scalar_one()
    org.oe_allow_recommend = True
    await db.commit()
    rid = (await client.post(f"/api/orders/{oid}/recommendations", headers=tenant,
                             json={"title": "Brakes", "kind": "custom", "media_data": PNG})).json()["id"]
    await client.post(f"/api/orders/{oid}/recommendations/{rid}/publish", headers=tenant)
    url = (await client.get(f"/api/public/track/{tok}")).json()["recommendations"][0]["media_data"]
    m = await client.get(url)
    assert m.status_code == 200 and m.content == RAW


async def test_replacing_or_deleting_a_recommendation_photo_empties_the_old_blob(client, tenant, db):
    from backend.models import MediaBlob, Recommendation
    from backend.services import media
    oid, _, _ = await _order(client, tenant)
    rid = (await client.post(f"/api/orders/{oid}/recommendations", headers=tenant,
                             json={"title": "Brakes", "kind": "custom", "media_data": PNG})).json()["id"]
    first = media.ref_id((await db.execute(select(Recommendation.media_data).where(
        Recommendation.id == rid))).scalar_one())
    r = await client.patch(f"/api/orders/{oid}/recommendations/{rid}", headers=tenant, json={"media_data": PNG})
    assert r.status_code == 200, r.text
    db.expire_all()
    assert (await db.get(MediaBlob, first)).inline_data is None
    second = media.ref_id((await db.execute(select(Recommendation.media_data).where(
        Recommendation.id == rid))).scalar_one())
    assert second and second != first
    assert (await client.delete(f"/api/orders/{oid}/recommendations/{rid}", headers=tenant)).status_code == 204
    db.expire_all()
    assert (await db.get(MediaBlob, second)).inline_data is None


async def test_gdpr_erase_destroys_a_stored_recommendation_photo(client, tenant, db):
    from backend.models import MediaBlob, Recommendation
    from backend.services import media
    oid, _, cid = await _order(client, tenant)
    rid = (await client.post(f"/api/orders/{oid}/recommendations", headers=tenant,
                             json={"title": "Brakes", "kind": "custom", "media_data": PNG})).json()["id"]
    blob_id = media.ref_id((await db.execute(select(Recommendation.media_data).where(
        Recommendation.id == rid))).scalar_one())
    r = await client.post(f"/api/compliance/gdpr/client/{cid}/erase", headers=tenant)
    assert r.status_code == 200, r.text
    db.expire_all()
    assert (await db.get(MediaBlob, blob_id)).inline_data is None
    assert (await db.execute(select(Recommendation.media_data).where(Recommendation.id == rid))).scalar_one() is None


# ── backfill ───────────────────────────────────────────────────────────────────────────────
async def test_backfill_moves_inline_rows_in_batches_and_is_idempotent(client, tenant, db, monkeypatch, tmp_path):
    from backend.config import settings
    from backend.models import EventFeedItem, MediaBlob, Recommendation
    from backend.services import media
    monkeypatch.setattr(settings, "MEDIA_BACKEND", "db")
    oid, _, _ = await _order(client, tenant)
    org = await _org_id(client, tenant)
    feed = [EventFeedItem(org_id=org, entity_type="order", entity_id=oid, item_type="photo",
                          media_data=PNG, media_name=f"{k}.png", media_mime="image/png") for k in range(3)]
    recs = [Recommendation(org_id=org, order_id=oid, title=f"R{k}", kind="custom", media_data=PNG)
            for k in range(2)]
    db.add_all(feed + recs)
    await db.commit()
    # an attachment already in the store under db, to be moved to local storage below
    up = await client.post("/api/media", headers=tenant, json={"data": PNG, "mime": "image/png"})
    db_blob = up.json()["id"]

    monkeypatch.setattr(settings, "MEDIA_BACKEND", "local")
    monkeypatch.setattr(settings, "MEDIA_LOCAL_DIR", str(tmp_path))
    statements = []
    out = await media.backfill(db, batch=2, org_id=org, on_batch=lambda: statements.append(1))
    assert out == {"feed": 3, "recommendations": 2, "blobs": 1}
    assert len(statements) >= 3                              # committed a batch at a time
    db.expire_all()
    for it in (await db.execute(select(EventFeedItem).where(EventFeedItem.id.in_([f.id for f in feed])))).scalars():
        assert it.media_data is None and it.media_blob_id
    for r in (await db.execute(select(Recommendation).where(Recommendation.order_id == oid))).scalars():
        assert media.ref_id(r.media_data)
    inline_left = (await db.execute(select(func.count()).select_from(MediaBlob).where(
        MediaBlob.org_id == org, MediaBlob.inline_data.is_not(None)))).scalar_one()
    assert inline_left == 0
    moved = await db.get(MediaBlob, db_blob)
    assert moved.backend == "local" and moved.storage_key
    assert (await client.get(f"/api/media/{db_blob}", headers=tenant)).content == RAW
    # every photo still opens
    for i in (await client.get(f"/api/feed/order/{oid}", headers=tenant)).json()["items"]:
        if i["media_url"]:
            assert (await client.get(i["media_url"], headers=tenant)).content == RAW
    # second run: nothing left to do
    assert await media.backfill(db, batch=2, org_id=org) == {"feed": 0, "recommendations": 0, "blobs": 0}


async def test_backfill_command_runs_from_the_cli(tmp_path):
    """The management command imports and parses its arguments (it is run against prod by hand)."""
    from backend.scripts import media_backfill
    args = media_backfill.parse(["--batch", "10", "--org", "abc", "--dry-run"])
    assert args.batch == 10 and args.org == "abc" and args.dry_run
