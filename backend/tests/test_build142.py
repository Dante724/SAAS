"""Build 142 — loyalty (points + gift cards + coupons) all surfaced under the client profile."""
import pytest


async def _client(client, h, name="Loyal"):
    return (await client.post("/api/clients", json={"name": name, "phone": "+15550142"}, headers=h)).json()["id"]


@pytest.mark.asyncio
async def test_giftcard_issued_to_client_is_tied_to_them(client, tenant):
    cid = await _client(client, tenant)
    r = await client.post("/api/giftcards", json={"value": 50, "client_id": cid}, headers=tenant)
    assert r.status_code == 201, r.text
    assert r.json()["client_id"] == cid and r.json()["balance"] == 50

    # the client's Loyalty tab filters the gift-card list by client_id
    cards = (await client.get("/api/giftcards", headers=tenant)).json()["items"]
    mine = [g for g in cards if g["client_id"] == cid]
    assert len(mine) == 1 and mine[0]["balance"] == 50


@pytest.mark.asyncio
async def test_coupons_list_available_for_client_tab(client, tenant):
    # a coupon the Loyalty tab lists as "available"
    r = await client.post("/api/coupons", json={"code": "SAVE10", "name": "10% off", "kind": "pct", "value": 10}, headers=tenant)
    assert r.status_code in (200, 201), r.text
    items = (await client.get("/api/coupons", headers=tenant)).json()["items"]
    c = next(x for x in items if x["code"] == "SAVE10")
    assert c["kind"] == "pct" and c["value"] == 10 and c["active"] is True


@pytest.mark.asyncio
async def test_client_loyalty_points_still_under_profile(client, tenant):
    cid = await _client(client, tenant, name="P")
    d = (await client.get(f"/api/clients/{cid}/loyalty", headers=tenant)).json()
    assert "points" in d and "tier" in d       # points remain on the client profile tab
