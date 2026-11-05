"""Backend follow-ups from the UI design audit.

- /api/auth/me sends the shop's country (the UI spells "Labor" for US shops, "Labour" elsewhere).
- Self check-in uses the same customer-page colour as Track and Book (page_color first).
- New API keys start with tk_ (Tekly); keys issued as rok_ (RepairOS) keep working.
"""
import hashlib

import pytest
from sqlalchemy import select


async def _org(db, client, h):
    from backend.models import Organisation
    me = (await client.get("/api/auth/me", headers=h)).json()
    return (await db.execute(select(Organisation).where(Organisation.id == me["org_id"]))).scalar_one()


@pytest.mark.asyncio
async def test_me_sends_the_shop_country(client, tenant, db):
    org = await _org(db, client, tenant)
    org.country = "US"
    await db.commit()
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    assert me["country"] == "US"


@pytest.mark.asyncio
async def test_checkin_uses_the_customer_page_colour(client, tenant, db):
    org = await _org(db, client, tenant)
    org.brand_primary_color = "#111111"
    org.page_color = "#0059ba"
    await db.commit()
    points = (await client.get("/api/checkins/points", headers=tenant)).json()["items"]
    token = points[0]["path"].split("/")[-1]
    r = await client.get(f"/api/public/checkin/{token}")
    assert r.status_code == 200, r.text
    assert r.json()["shop"]["color"] == "#0059ba"

    org.page_color = ""                        # no page colour: falls back to the brand colour
    await db.commit()
    assert (await client.get(f"/api/public/checkin/{token}")).json()["shop"]["color"] == "#111111"


@pytest.mark.asyncio
async def test_new_keys_start_with_tk_and_old_rok_keys_still_work(client, tenant, db):
    from backend.models import ApiKey
    r = await client.post("/api/api-keys", json={"name": "zap", "scopes": "read"}, headers=tenant)
    assert r.status_code == 201, r.text
    body = r.json()
    key = body.get("key") or body.get("token") or body.get("plaintext")
    assert key.startswith("tk_")
    assert (await client.get("/api/v1/contacts", headers={"Authorization": f"Bearer {key}"})).status_code == 200

    # A key issued before the rename: same row shape, rok_ prefix. It must keep working.
    legacy = "rok_abcd1234_" + "x" * 32
    row = (await db.execute(select(ApiKey).where(ApiKey.key_hash == hashlib.sha256(key.encode()).hexdigest()))).scalar_one()
    row.key_hash = hashlib.sha256(legacy.encode()).hexdigest()
    row.prefix = "rok_abcd1234"
    await db.commit()
    assert (await client.get("/api/v1/contacts", headers={"Authorization": f"Bearer {legacy}"})).status_code == 200

    # Made-up tokens with either prefix are still refused.
    for fake in ("tk_zzzzzzzz_nope", "rok_zzzzzzzz_nope"):
        assert (await client.get("/api/v1/contacts", headers={"Authorization": f"Bearer {fake}"})).status_code == 401


@pytest.mark.asyncio
async def test_location_revenue_adds_up_to_business_insights(client, tenant, db):
    """Business & analytics showed Revenue ₹1,200 next to "Main Shop · 1 closed · ₹9,400": the
    location rows used their own rule (status-based, total with tax). They now use the shared one."""
    r = await client.post("/api/statuses", json={
        "name": "Closed DF", "key": "closed-df", "status_group": "closed", "is_terminal": True}, headers=tenant)
    assert r.status_code == 201, r.text
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    for amount in (150.0, 250.0):
        o = (await client.post("/api/orders", json={"location_id": loc, "complaint": "x"}, headers=tenant)).json()
        await client.post(f"/api/orders/{o['id']}/lines", json={
            "line_type": "labour", "kind": "custom", "description": "job", "qty": 1, "unit_price": amount}, headers=tenant)
        assert (await client.patch(f"/api/orders/{o['id']}/status", json={"status": "closed-df"},
                                   headers=tenant)).status_code == 200
    ins = (await client.get("/api/analytics/business-insights?days=30", headers=tenant)).json()
    comp = (await client.get("/api/analytics/location-comparison?days=30", headers=tenant)).json()
    assert sum(l["orders_closed"] for l in comp["locations"]) == ins["orders_closed"] == 2
    assert sum(l["revenue"] for l in comp["locations"]) == pytest.approx(ins["revenue"])
    tr = (await client.get("/api/analytics/trends?days=30", headers=tenant)).json()
    assert sum(p["revenue"] for p in tr["series"]) == pytest.approx(ins["revenue"])
