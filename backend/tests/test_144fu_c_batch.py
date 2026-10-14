"""C — public-link rotate/revoke + duplicate-phone surfacing."""
import pytest


@pytest.mark.asyncio
async def test_public_link_rotate_and_revoke(client, tenant):
    h = tenant
    cid = (await client.post("/api/clients", json={"name": "C", "phone": "+1500"}, headers=h)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid}, headers=h)).json()["id"]

    t1 = (await client.post(f"/api/orders/{oid}/public-link", headers=h)).json()["token"]
    assert t1
    # rotate → new token, old one invalidated
    t2 = (await client.post(f"/api/orders/{oid}/public-link/rotate", headers=h)).json()["token"]
    assert t2 and t2 != t1
    # revoke → no token
    r = await client.post(f"/api/orders/{oid}/public-link/revoke", headers=h)
    assert r.status_code == 200 and r.json()["revoked"] is True
    # the revoked/old tokens no longer resolve on the public track page
    assert (await client.get(f"/api/public/track/{t1}")).status_code in (404, 401, 403)
    assert (await client.get(f"/api/public/track/{t2}")).status_code in (404, 401, 403)


@pytest.mark.asyncio
async def test_duplicate_phone_surface(client, tenant):
    h = tenant
    for name in ("Ann", "Ann Dup"):
        await client.post("/api/clients", json={"name": name, "phone": "+1999"}, headers=h)
    await client.post("/api/clients", json={"name": "Solo", "phone": "+1777"}, headers=h)
    r = await client.get("/api/clients/duplicates", headers=h)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 1
    cluster = items[0]
    assert cluster["phone"] == "+1999" and cluster["count"] == 2
    assert cluster["keep_hint_id"] == cluster["clients"][-1]["id"]
