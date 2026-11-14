"""Owner decision 8: a customer tracking link stops working 90 days after its job is closed.

One platform default (settings.TRACK_LINK_DAYS, 90), no per-shop setting. An expired link answers
410 Gone with ``expired: true`` and the shop's public contact only (never job data, never a 404 or a
500), on the track page and on every route keyed by the same token (actions, PDFs, photos). An open
job never expires; a job that is reopened is open again, so its link works again.
"""
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from backend.config import settings
from backend.models import Order, Organisation
from backend.services.dates import now_utc


async def _order(client, h, with_client=False):
    loc = (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]
    cl = (await client.post("/api/clients", json={"name": "Expiree", "phone": "5550001111"}, headers=h)).json()["id"]
    r = await client.post("/api/orders", json={"client_id": cl, "location_id": loc, "complaint": "cracked screen"},
                          headers=h)
    assert r.status_code == 201, r.text
    o = r.json()
    return (o["id"], o["public_token"], cl) if with_client else (o["id"], o["public_token"])


async def _status(client, h, group):
    sts = (await client.get("/api/statuses", headers=h)).json()
    rows = sts.get("items", sts) if isinstance(sts, dict) else sts
    return next(s["key"] for s in rows if s.get("status_group") == group
                and (s.get("record_type") in (None, "work_order")))


async def _set_status(client, h, oid, group):
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": await _status(client, h, group)}, headers=h)
    assert r.status_code == 200, r.text


async def _close(client, h, db, oid, days_ago):
    """Close the job for real (its status), then move the close back in time. The link was issued
    when the job was created, before the close."""
    await _set_status(client, h, oid, "closed")
    closed = now_utc() - timedelta(days=days_ago)
    await db.execute(update(Order).where(Order.id == oid).values(
        closed_at=closed, created_at=closed - timedelta(days=3), public_token_issued_at=closed - timedelta(days=3)))
    await db.commit()


async def test_the_default_window_is_90_days():
    assert settings.TRACK_LINK_DAYS == 90


async def test_an_open_job_works_however_old_it_is(client, tenant, db):
    oid, tok = await _order(client, tenant)
    long_ago = now_utc() - timedelta(days=5 * 365)
    await db.execute(update(Order).where(Order.id == oid).values(created_at=long_ago, public_token_issued_at=long_ago))
    await db.commit()
    r = await client.get(f"/api/public/track/{tok}")
    assert r.status_code == 200, r.text
    assert r.json()["order_number"]


async def test_closed_89_days_ago_still_works(client, tenant, db):
    oid, tok = await _order(client, tenant)
    await _close(client, tenant, db, oid, 89)
    r = await client.get(f"/api/public/track/{tok}")
    assert r.status_code == 200, r.text
    assert (await client.get(f"/api/public/track/{tok}/einvoice.xml")).status_code != 410


async def test_closed_91_days_ago_answers_the_expired_page(client, tenant, db):
    oid, tok = await _order(client, tenant)
    org_id = (await db.execute(select(Order.org_id).where(Order.id == oid)
                               .execution_options(skip_tenant_filter=True))).scalar_one()
    await db.execute(update(Organisation).where(Organisation.id == org_id).values(
        name="Fix Co", company_phone="+1 555 0100", company_email="hello@fixco.test"))
    await db.commit()
    await _close(client, tenant, db, oid, 91)

    r = await client.get(f"/api/public/track/{tok}")
    assert r.status_code == 410, r.text
    body = r.json()
    assert body["expired"] is True
    assert "expired" in body["detail"].lower() and "contact the shop" in body["detail"].lower()
    assert body["shop"]["name"] == "Fix Co"
    assert body["shop"]["email"] == "hello@fixco.test"
    assert "phone" in body["shop"]
    # The shop's public contact only: no job data at all.
    assert set(body) == {"detail", "expired", "shop"}
    assert set(body["shop"]) <= {"name", "phone", "email"}
    text = r.text
    for leak in ("cracked screen", "Expiree", "order_number", "total", "5550001111"):
        assert leak not in text, leak


ROUTES = [
    ("get", "", None),
    ("post", "/respond", {"decision": "accept"}),
    ("post", "/deposit", None),
    ("get", "/certificate.pdf", None),
    ("get", "/einvoice.xml", None),
    ("post", "/message", {"body": "hello?"}),
    ("post", "/new-request", {"body": "another repair"}),
    ("post", "/feedback", {"rating": 5}),
    ("post", "/recommendations/decide", {"approve_ids": []}),
]


async def test_every_route_on_an_expired_token_is_refused(client, tenant, db):
    oid, tok = await _order(client, tenant)
    await _close(client, tenant, db, oid, 91)
    for method, sub, body in ROUTES:
        kw = {"json": body} if body is not None else {}
        r = await getattr(client, method)(f"/api/public/track/{tok}{sub}", **kw)
        assert r.status_code == 410, (sub, r.status_code, r.text)
        assert r.json()["expired"] is True, sub
    for sub in ("/feed/anything", "/rec/anything"):
        r = await client.get(f"/api/media/track/{tok}{sub}")
        assert r.status_code == 410, (sub, r.status_code, r.text)
        assert r.json()["expired"] is True


async def test_photos_stop_with_the_link(client, tenant, db):
    import base64
    oid, tok = await _order(client, tenant)
    png = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"A" * 64).decode()
    r = await client.post(f"/api/feed/order/{oid}", headers=tenant, json={
        "item_type": "photo", "media_data": png, "media_name": "p.png", "media_mime": "image/png",
        "visibility": "client"})
    assert r.status_code == 201, r.text
    url = f"/api/media/track/{tok}/feed/{r.json()['id']}"
    assert (await client.get(url)).status_code == 200
    await _close(client, tenant, db, oid, 91)
    m = await client.get(url)
    assert m.status_code == 410
    assert not m.content.startswith(b"\x89PNG")


async def test_a_reopened_job_works_again(client, tenant, db):
    oid, tok = await _order(client, tenant)
    await _close(client, tenant, db, oid, 200)
    assert (await client.get(f"/api/public/track/{tok}")).status_code == 410
    await _set_status(client, tenant, oid, "in_progress")       # reopened: open again
    assert (await client.get(f"/api/public/track/{tok}")).status_code == 200
    assert (await client.get(f"/api/media/track/{tok}/feed/none")).status_code == 404   # the route is live again
    await _set_status(client, tenant, oid, "closed")             # closed again today: a fresh 90 days
    assert (await client.get(f"/api/public/track/{tok}")).status_code == 200


@pytest.mark.parametrize("days, closed_ago, expected", [(30, 31, 410), (30, 29, 200), (365, 200, 200)])
async def test_the_setting_changes_the_window(client, tenant, db, monkeypatch, days, closed_ago, expected):
    monkeypatch.setattr(settings, "TRACK_LINK_DAYS", days)
    oid, tok = await _order(client, tenant)
    await _close(client, tenant, db, oid, closed_ago)
    assert (await client.get(f"/api/public/track/{tok}")).status_code == expected


async def test_the_portal_hides_the_track_extras_of_an_expired_job(client, tenant, db):
    """The portal's own job view uses the portal link, not the track token; for an expired job it
    stops handing out the track token, so its rating / PDF / e-invoice parts hide instead of failing."""
    oid, _tok, cid = await _order(client, tenant, with_client=True)
    await _close(client, tenant, db, oid, 91)
    r = await client.post(f"/api/clients/{cid}/portal-link", headers=tenant)
    assert r.status_code == 200, r.text
    ptok = r.json()["portal_token"]
    j = await client.get(f"/api/public/portal/{ptok}/order/{oid}")
    assert j.status_code == 200, j.text
    assert j.json()["public_token"] is None
