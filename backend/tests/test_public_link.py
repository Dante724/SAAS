"""Copy Public Link — the order gets a secure tracking token minted on demand.

An order may have no public_token (older/seeded rows). POST /public-link must
mint a secure, unguessable token, persist it, return /track/{token}, be
idempotent, and the token must resolve on the public track endpoint.
"""
from sqlalchemy import select

from backend.models import Order


async def _loc_id(client, H):
    d = (await client.get("/api/staff/locations", headers=H)).json()
    items = d if isinstance(d, list) else d.get("items", d)
    return items[0]["id"]


async def _client_id(client, H):
    r = await client.post("/api/clients", json={"name": "Trackee", "phone": "+10000000009"}, headers=H)
    return r.json()["id"]


async def _make_order(client, H):
    body = {"client_id": await _client_id(client, H), "location_id": await _loc_id(client, H),
            "complaint": "won't boot", "lines": []}
    r = await client.post("/api/orders", json=body, headers=H)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_public_link_minted_when_missing(client, tenant, db):
    oid = await _make_order(client, tenant)
    # force the token to be absent (simulates an older/seeded order)
    o = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    o.public_token = None
    await db.commit()

    r = await client.post(f"/api/orders/{oid}/public-link", json={}, headers=tenant)
    assert r.status_code == 200, r.text
    body = r.json()
    tok = body["token"]
    assert tok and len(tok) >= 16                      # unguessable, not empty
    assert body["path"] == f"/track/{tok}"

    # persisted
    o2 = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    await db.refresh(o2)
    assert o2.public_token == tok


async def test_public_link_is_idempotent(client, tenant):
    oid = await _make_order(client, tenant)
    a = (await client.post(f"/api/orders/{oid}/public-link", json={}, headers=tenant)).json()
    b = (await client.post(f"/api/orders/{oid}/public-link", json={}, headers=tenant)).json()
    assert a["token"] == b["token"]                     # same token, not regenerated


async def test_public_link_resolves_on_track(client, tenant):
    oid = await _make_order(client, tenant)
    tok = (await client.post(f"/api/orders/{oid}/public-link", json={}, headers=tenant)).json()["token"]
    # the public (unauthenticated) track endpoint resolves the token
    t = await client.get(f"/api/public/track/{tok}")
    assert t.status_code == 200, t.text


async def test_public_link_404_for_other_org(client, tenant):
    oid = await _make_order(client, tenant)
    # a different org must not be able to mint a link for someone else's order
    import uuid
    email = f"other-{uuid.uuid4().hex[:8]}@test.dev"
    reg = await client.post("/api/auth/register", json={
        "org_name": "Other Co", "name": "Admin", "email": email, "password": "pw-test-12345"})
    other = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    r = await client.post(f"/api/orders/{oid}/public-link", json={}, headers=other)
    assert r.status_code == 404
