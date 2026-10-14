"""144di/144dj — caching policy for the public appointment pages, multi-tenant safe.

- Low-churn shop config (branding/services) gets a short public cache.
- Availability (/slots, /days) reflects the WHOLE shop's live appointments, so it is
  never cached or shared — always live, never served stale to another visitor.
- The live track page (secret-token keyed) is never shared-cached.
- Tenant isolation: each shop's appointment URL carries its own unique slug, so one
  shop's page can never be served for, or clash with, another shop's.
"""
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError


async def _enable_appointment(db):
    from backend.models import Organisation
    org = (await db.execute(select(Organisation))).scalars().first()
    org.appointment_enabled = True
    await db.commit()
    return org


async def test_appointment_config_is_publicly_cacheable(client, tenant, db):
    org = await _enable_appointment(db)
    r = await client.get(f"/api/public/appointment/{org.id}")
    assert r.status_code == 200, r.text
    assert r.headers.get("cache-control") == "public, max-age=60"


async def test_availability_is_never_shared_cached(client, tenant, db):
    """Whole-shop live data → no-store, so an appointment is never hidden behind a
    stale cache for other visitors."""
    org = await _enable_appointment(db)
    rd = await client.get(f"/api/public/appointment/{org.id}/days?days=3")
    assert rd.status_code == 200, rd.text
    assert rd.headers.get("cache-control") == "no-store"

    rs = await client.get(f"/api/public/appointment/{org.id}/slots?date=2099-01-15")
    assert rs.status_code == 200, rs.text
    assert rs.headers.get("cache-control") == "no-store"


async def test_track_page_is_not_cached(client, tenant, db):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={"name": "C"}, headers=tenant)).json()["id"]
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc,
                                                 "complaint": "x"}, headers=tenant)).json()
    r = await client.get(f"/api/public/track/{o['public_token']}")
    assert r.status_code == 200, r.text
    assert "public" not in r.headers.get("cache-control", "")


async def test_appointment_url_is_tenant_unique_and_never_crosses(client, tenant, db):
    """Two shops → two distinct, unique slugs. Each appointment URL returns only its
    own shop's data; a slug can never resolve to the other tenant."""
    from backend.models import Organisation
    # tenant fixture gives shop A; make a second shop B directly.
    orgA = (await db.execute(select(Organisation))).scalars().first()
    orgA.appointment_enabled = True
    orgA.name = "Shop A"
    orgA.subdomain = "shop-a"
    orgB = Organisation(name="Shop B", plan="free", subdomain="shop-b",
                        currency="$", currency_code="USD", timezone="UTC",
                        appointment_enabled=True)
    db.add(orgB)
    await db.commit()

    # Unique slugs, and the unique constraint forbids a duplicate.
    assert orgA.subdomain != orgB.subdomain

    a = await client.get(f"/api/public/appointment/{orgA.subdomain}")
    b = await client.get(f"/api/public/appointment/{orgB.subdomain}")
    assert a.status_code == 200 and b.status_code == 200
    assert a.json()["shop_name"] == "Shop A"
    assert b.json()["shop_name"] == "Shop B"
    # A's slug never yields B's data and vice-versa.
    assert a.json()["shop_name"] != b.json()["shop_name"]

    # Re-pointing a slug stays unique: cannot collide with the other tenant's.
    with pytest.raises(IntegrityError):
        orgB.subdomain = "shop-a"
        await db.commit()
