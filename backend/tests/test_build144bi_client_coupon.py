"""Build 144bi — returning-customer coupon loop: a coupon issued to a client
redeems only on that client's order, and the WO logs an amount-changed event."""
import uuid

import pytest


async def _tenant(client):
    email = f"admin-{uuid.uuid4().hex[:8]}@test.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Bi Co", "name": "Admin", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _loc(client, H):
    d = (await client.get("/api/staff/locations", headers=H)).json()
    return (d if isinstance(d, list) else d["items"])[0]["id"]


async def _order_for(client, H, loc, client_id):
    return (await client.post("/api/orders", json={
        "client_id": client_id, "location_id": loc, "complaint": "x"}, headers=H)).json()


async def test_client_coupon_redeems_only_for_that_client(client):
    H = await _tenant(client)
    loc = await _loc(client, H)
    neha = (await client.post("/api/clients", json={"name": "Neha"}, headers=H)).json()["id"]
    raj = (await client.post("/api/clients", json={"name": "Raj"}, headers=H)).json()["id"]
    # mint a 10% coupon for Neha
    cp = (await client.post(f"/api/clients/{neha}/coupons", json={"kind": "pct", "value": 10}, headers=H)).json()
    assert cp["code"].startswith("C-")
    # it shows under Neha's coupons, not Raj's
    neha_cps = (await client.get(f"/api/clients/{neha}/coupons", headers=H)).json()["items"]
    raj_cps = (await client.get(f"/api/clients/{raj}/coupons", headers=H)).json()["items"]
    assert any(c["code"] == cp["code"] for c in neha_cps)
    assert not raj_cps
    # Raj's order can't redeem Neha's coupon
    ro = await _order_for(client, H, loc, raj)
    await client.post(f"/api/orders/{ro['id']}/lines", json={
        "line_type": "part", "kind": "product", "description": "Part", "qty": 1, "unit_price": 1000}, headers=H)
    bad = await client.post(f"/api/orders/{ro['id']}/apply-coupon", json={"code": cp["code"]}, headers=H)
    assert bad.status_code == 403
    # Neha's order can, and it discounts 10%
    no = await _order_for(client, H, loc, neha)
    await client.post(f"/api/orders/{no['id']}/lines", json={
        "line_type": "part", "kind": "product", "description": "Part", "qty": 1, "unit_price": 1000}, headers=H)
    ok = await client.post(f"/api/orders/{no['id']}/apply-coupon", json={"code": cp["code"]}, headers=H)
    assert ok.status_code == 200
    assert ok.json()["discount"] == 100.0
    # single-use: a second redeem is refused
    again = await client.post(f"/api/orders/{no['id']}/apply-coupon", json={"code": cp["code"]}, headers=H)
    assert again.status_code == 400


async def test_amount_changed_event_logged(client):
    H = await _tenant(client)
    loc = await _loc(client, H)
    cid = (await client.post("/api/clients", json={"name": "Cust"}, headers=H)).json()["id"]
    o = await _order_for(client, H, loc, cid)
    await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "part", "kind": "product", "description": "Battery", "qty": 1, "unit_price": 500}, headers=H)
    feed = (await client.get(f"/api/feed/order/{o['id']}", headers=H)).json()["items"]
    assert any("Amount changed" in (f.get("body") or "") and "→" in (f.get("body") or "") for f in feed)


async def test_order_detail_exposes_client_balance(client):
    H = await _tenant(client)
    loc = await _loc(client, H)
    cid = (await client.post("/api/clients", json={"name": "Cust"}, headers=H)).json()["id"]
    o = await _order_for(client, H, loc, cid)
    got = (await client.get(f"/api/orders/{o['id']}", headers=H)).json()
    assert "balance" in got["client"]
