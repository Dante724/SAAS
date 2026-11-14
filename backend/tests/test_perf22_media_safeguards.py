"""PERF-22 (owner decision 4): "unlimited" attachment storage with safeguards nobody sees.

1. No shop-visible storage cap.
2. One 10 MB limit per file, enforced on every upload path, answered with 413 and a clear message;
   the frontend reads the same number (frontend-react/src/lib/media.ts).
3. Photos are shrunk to ~2000 px on the long side, re-encoded without EXIF (orientation applied).
   Non-images and photos we can't decode pass through unchanged.
4. Past MEDIA_ALERT_BYTES of stored media, the platform admins get one alert per shop per crossing.
5. A deployed server never keeps attachment bytes in the database: with no object storage, uploads
   fail closed (503) and the server says so at startup and on /api/readyz.
"""
import base64
import io
import logging
import pathlib
import re

import pytest
from PIL import Image
from sqlalchemy import func, select

from backend.config import settings
from backend.services import media

ROOT = pathlib.Path(__file__).resolve().parents[2]
SMALL_CAP = 64 * 1024


def _data_url(raw: bytes, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(raw).decode()


def _jpeg(w: int, h: int, *, exif: dict | None = None, quality: int = 95) -> bytes:
    img = Image.new("RGB", (w, h))
    # a gradient, so the encoder has something to compress (a flat image is a few KB at any size)
    px = img.load()
    for x in range(0, w, 7):
        for y in range(0, h, 7):
            px[x, y] = (x % 256, y % 256, (x * y) % 256)
    out = io.BytesIO()
    kw = {"quality": quality}
    if exif:
        e = Image.Exif()
        for k, v in exif.items():
            e[k] = v
        kw["exif"] = e.tobytes()
    img.save(out, "JPEG", **kw)
    return out.getvalue()


def _big(n: int) -> str:
    return _data_url(b"%PDF-1.4 " + b"x" * n, "application/pdf")


async def _order(client, tenant):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={"name": "T"}, headers=tenant)).json()["id"]
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"},
                           headers=tenant)).json()
    return o["id"], cid, loc


# ── 2. one limit, everywhere ────────────────────────────────────────────────────────────────

def test_the_limit_is_10_mb_and_the_frontend_uses_the_same_number():
    assert settings.MEDIA_MAX_BYTES == 10 * 1024 * 1024
    src = (ROOT / "frontend-react/src/lib/media.ts").read_text()
    m = re.search(r"export const MEDIA_MAX_MB = (\d+);", src)
    assert m, "frontend-react/src/lib/media.ts must export MEDIA_MAX_MB"
    assert int(m.group(1)) * 1024 * 1024 == settings.MEDIA_MAX_BYTES
    # a base64 upload of a full-size file (plus its JSON) must fit under the request cap
    assert settings.MAX_REQUEST_BYTES > settings.MEDIA_MAX_BYTES * 4 // 3 + 64 * 1024


def test_message_names_the_limit():
    assert media.too_large_message() == "That file is over the 10 MB limit. Try a smaller file."


@pytest.fixture
def small_cap(monkeypatch):
    monkeypatch.setattr(settings, "MEDIA_MAX_BYTES", SMALL_CAP)
    return _big(SMALL_CAP + 1000)


def _is_limit(r):
    assert r.status_code == 413, (r.status_code, r.text)
    assert "64 KB" in r.json()["detail"] or "limit" in r.json()["detail"], r.text


async def test_generic_upload_over_the_limit_is_413(client, tenant, small_cap):
    _is_limit(await client.post("/api/media", headers=tenant, json={"data": small_cap, "name": "a.pdf"}))
    ok = await client.post("/api/media", headers=tenant, json={"data": _big(1000), "name": "a.pdf"})
    assert ok.status_code == 201, ok.text


async def test_feed_and_recommendation_over_the_limit_are_413(client, tenant, small_cap):
    oid, _, _ = await _order(client, tenant)
    _is_limit(await client.post(f"/api/feed/order/{oid}", headers=tenant, json={
        "item_type": "file", "media_data": small_cap, "media_name": "a.pdf", "media_mime": "application/pdf"}))


async def test_supplier_invoice_over_the_limit_is_413_not_500(client, tenant, small_cap):
    sid = (await client.post("/api/suppliers", json={"name": "Acme"}, headers=tenant)).json()["id"]
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    pid = (await client.post("/api/inventory/parts", json={"name": "Belt", "sku": "B-1"}, headers=tenant)).json()["id"]
    po_id = (await client.post("/api/inventory/po", headers=tenant, json={
        "supplier_id": sid, "location_id": loc,
        "lines": [{"part_id": pid, "qty_ordered": 1, "unit_cost": 5}]})).json()["po_id"]
    _is_limit(await client.post(f"/api/inventory/po/{po_id}/invoice", headers=tenant, json={
        "supplier_invoice_total": 5, "invoice_file": small_cap, "invoice_name": "inv.pdf"}))


async def test_asset_photo_over_the_limit_is_413_on_create_update_and_inline(client, tenant, small_cap):
    oid, cid, loc = await _order(client, tenant)
    big_img = _data_url(b"\xff\xd8\xff" + b"x" * (SMALL_CAP + 1000), "image/jpeg")
    _is_limit(await client.post("/api/assets", headers=tenant, json={
        "client_id": cid, "brand": "A", "model": "B", "image_data": big_img}))
    aid = (await client.post("/api/assets", headers=tenant, json={"client_id": cid, "brand": "A", "model": "B"})).json()["id"]
    _is_limit(await client.put(f"/api/assets/{aid}", headers=tenant, json={"image_data": big_img}))
    _is_limit(await client.post("/api/orders", headers=tenant, json={
        "client_id": cid, "location_id": loc, "complaint": "x", "asset_brand": "A", "asset_image": big_img}))


async def test_logo_over_the_limit_is_413(client, tenant, small_cap):
    big_img = _data_url(b"\x89PNG\r\n\x1a\n" + b"x" * (SMALL_CAP + 1000), "image/png")
    _is_limit(await client.put("/api/staff/company", headers=tenant, json={"logo_data": big_img}))


async def test_check_in_photo_over_the_limit_is_413_with_the_real_limit(client, monkeypatch):
    from backend.tests import test_self_checkin as sc
    h = await sc._register(client)
    s = await sc._setup(client, h)
    monkeypatch.setattr(settings, "MEDIA_MAX_BYTES", SMALL_CAP)
    big = _data_url(b"\xff\xd8\xff" + b"x" * (SMALL_CAP + 1000), "image/jpeg")
    r = await client.post(f"/api/public/checkin/{s['token']}", json=sc._body(s, photos=[big]))
    assert r.status_code == 413, r.text
    assert "5 MB" not in r.json()["detail"]


async def test_an_oversize_request_body_names_the_per_file_limit(client, tenant, monkeypatch):
    from backend.core.body_limit import BodySizeLimitMiddleware
    sent = []

    async def app(scope, receive, send):  # never reached
        raise AssertionError
    mw = BodySizeLimitMiddleware(app, max_bytes=100)

    async def send(msg):
        sent.append(msg)
    scope = {"type": "http", "headers": [(b"content-length", b"1000")]}
    await mw(scope, None, send)
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    assert sent[0]["status"] == 413
    assert b"10 MB" in body


# ── 3. photos shrink on the server too ──────────────────────────────────────────────────────

async def _stored(db, blob):
    raw, mime = await media.load(blob)
    return raw, mime


async def test_a_large_photo_is_shrunk_to_2000px_without_exif(db, tenant):
    from backend.models import Organisation
    org = (await db.execute(select(Organisation).limit(1))).scalar_one()
    # orientation 6 = the camera was turned: the shown photo is 1500 wide, 3000 tall
    raw = _jpeg(3000, 1500, exif={0x0112: 6, 0x010F: "PhoneCo"})
    blob = await media.store(db, org.id, _data_url(raw, "image/jpeg"), name="p.jpg", mime="image/jpeg")
    got, mime = await _stored(db, blob)
    img = Image.open(io.BytesIO(got))
    assert max(img.size) == 2000 and img.size == (1000, 2000), img.size   # turned upright, then shrunk
    assert not img.getexif(), dict(img.getexif())
    assert mime in ("image/jpeg", "image/webp") and blob.size_bytes == len(got) < len(raw)


async def test_a_small_photo_with_camera_exif_is_cleaned(db, tenant):
    from backend.models import Organisation
    org = (await db.execute(select(Organisation).limit(1))).scalar_one()
    raw = _jpeg(800, 600, exif={0x010F: "PhoneCo", 0x0110: "Model X", 0x0131: "Camera 1.0"})
    blob = await media.store(db, org.id, _data_url(raw, "image/jpeg"), name="p.jpg", mime="image/jpeg")
    got, _ = await _stored(db, blob)
    img = Image.open(io.BytesIO(got))
    assert img.size == (800, 600) and not img.getexif()


async def test_a_small_clean_photo_is_kept_as_it_is(db, tenant):
    from backend.models import Organisation
    org = (await db.execute(select(Organisation).limit(1))).scalar_one()
    raw = _jpeg(800, 600)
    blob = await media.store(db, org.id, _data_url(raw, "image/jpeg"), name="p.jpg", mime="image/jpeg")
    assert (await _stored(db, blob))[0] == raw


async def test_pdfs_and_unreadable_images_pass_through(db, tenant):
    from backend.models import Organisation
    org = (await db.execute(select(Organisation).limit(1))).scalar_one()
    pdf = b"%PDF-1.4 " + b"y" * 5000
    fake_png = b"\x89PNG\r\n\x1a\n" + b"A" * 5000
    for raw, mime in ((pdf, "application/pdf"), (fake_png, "image/png")):
        blob = await media.store(db, org.id, _data_url(raw, mime), mime=mime)
        assert (await _stored(db, blob))[0] == raw


# ── 5. a deployed server never stores bytes in the database ─────────────────────────────────

@pytest.mark.parametrize("backend,bucket", [("db", ""), ("s3", "")])
def test_deployed_without_object_storage_has_a_storage_problem(monkeypatch, backend, bucket):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "MEDIA_BACKEND", backend)
    monkeypatch.setattr(settings, "MEDIA_S3_BUCKET", bucket)
    assert media.storage_problem()
    assert "MEDIA_BACKEND" in media.storage_problem()


def test_deployed_with_object_storage_or_in_dev_is_fine(monkeypatch):
    monkeypatch.setattr(settings, "MEDIA_BACKEND", "s3")
    monkeypatch.setattr(settings, "MEDIA_S3_BUCKET", "bucket")
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    assert media.storage_problem() is None
    monkeypatch.setattr(settings, "MEDIA_BACKEND", "db")
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    assert media.storage_problem() is None


async def test_deployed_upload_without_object_storage_fails_closed(client, tenant, db, monkeypatch):
    from backend.models import MediaBlob
    before = (await db.execute(select(func.count(MediaBlob.id)))).scalar()
    old = await client.post("/api/media", headers=tenant, json={"data": _big(100), "name": "old.pdf"})
    assert old.status_code == 201
    monkeypatch.setattr(media, "_deployed", lambda: True)
    monkeypatch.setattr(settings, "MEDIA_BACKEND", "db")
    r = await client.post("/api/media", headers=tenant, json={"data": _big(100), "name": "a.pdf"})
    assert r.status_code == 503, r.text
    assert "storage" in r.json()["detail"].lower()
    db.expire_all()
    assert (await db.execute(select(func.count(MediaBlob.id)))).scalar() == before + 1   # only the old one
    # files saved before keep opening
    assert (await client.get(old.json()["url"], headers=tenant)).status_code == 200


async def test_readyz_and_startup_say_when_storage_is_missing(client, monkeypatch, caplog):
    monkeypatch.setattr(media, "_deployed", lambda: True)
    monkeypatch.setattr(settings, "MEDIA_BACKEND", "db")
    r = (await client.get("/api/readyz")).json()
    assert r["media_store"] == "not configured"
    with caplog.at_level(logging.ERROR, logger="tekly.media"):
        media.warn_if_unconfigured()
    assert any("MEDIA_BACKEND" in rec.getMessage() for rec in caplog.records)
    monkeypatch.setattr(media, "_deployed", lambda: False)
    assert (await client.get("/api/readyz")).json()["media_store"] == "ok"


# ── 4. a private alert for the platform admins ──────────────────────────────────────────────

async def _shop_with_bytes(db, n_bytes: int):
    from backend.models import MediaBlob, Organisation
    org = Organisation(name="Big shop", plan="free", currency="$", currency_code="USD", timezone="UTC")
    db.add(org)
    await db.flush()
    db.add(MediaBlob(org_id=org.id, backend="local", storage_key=f"k/{org.id}", size_bytes=n_bytes))
    await db.commit()
    return org


async def test_alert_fires_once_per_crossing_and_again_after_dropping_below(db, monkeypatch, caplog):
    from backend.models import MediaBlob, MediaUsageAlert
    from backend.services import media_usage
    monkeypatch.setattr(settings, "MEDIA_ALERT_BYTES", 1000)
    big = await _shop_with_bytes(db, 1500)
    small = await _shop_with_bytes(db, 500)

    with caplog.at_level(logging.ERROR, logger="tekly.media"):
        assert await media_usage.sweep(db) == 1
        assert await media_usage.sweep(db) == 0          # still over: no second alert
    hits = [r for r in caplog.records if big.id in r.getMessage()]
    assert len(hits) == 1
    assert not any(small.id in r.getMessage() for r in caplog.records)
    alerts = (await db.execute(select(MediaUsageAlert))).scalars().all()
    assert [(a.org_id, a.cleared_at is None) for a in alerts] == [(big.id, True)]

    # the shop deletes files and drops below: the alert closes; crossing again opens a new one
    blob = (await db.execute(select(MediaBlob).where(MediaBlob.org_id == big.id))).scalar_one()
    blob.size_bytes = 100
    await db.commit()
    assert await media_usage.sweep(db) == 0
    blob.size_bytes = 2000
    await db.commit()
    assert await media_usage.sweep(db) == 1
    big_id = blob.org_id
    db.expire_all()
    rows = (await db.execute(select(MediaUsageAlert).where(MediaUsageAlert.org_id == big_id))).scalars().all()
    assert sorted(r.cleared_at is None for r in rows) == [False, True]   # one closed, one open


async def test_alert_shows_for_platform_admins_and_in_metrics_never_for_the_shop(client, tenant, db, monkeypatch):
    from backend.core.observability import render_metrics
    from backend.services import media_usage
    from backend.tests.admin_helpers import admin_headers
    monkeypatch.setattr(settings, "MEDIA_ALERT_BYTES", 1000)
    big = await _shop_with_bytes(db, 5000)
    await media_usage.sweep(db)

    H, _ = await admin_headers(client)
    stats = (await client.get("/api/admin/stats", headers=H)).json()
    assert [a["org_id"] for a in stats["media_alerts"]] == [big.id]
    assert stats["media_alerts"][0]["bytes"] == 5000
    detail = (await client.get(f"/api/admin/tenants/{big.id}", headers=H)).json()
    assert detail["media_bytes"] == 5000 and detail["media_alert"] is True
    assert "repairos_media_shops_over_threshold 1" in render_metrics()

    # the shop's own storage status carries no cap, quota or alert
    s = (await client.get("/api/media/status", headers=tenant)).json()
    assert not ({"quota", "limit", "alert", "media_alert", "cap"} & set(s))
