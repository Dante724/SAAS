"""144p5b — full gift-card flow the Client drawer relies on:
issue → shows in GET /giftcards for that client → redeem on an order drops balance → expired card rejected."""
from datetime import datetime, timedelta

from backend.services.dates import now_utc


async def _client(client, tenant, name="Flow Co"):
    return (await client.post("/api/clients", json={"name": name, "phone": "+15550555"}, headers=tenant)).json()["id"]


async def _order_with_total(client, tenant, cid):
    r = await client.post("/api/orders", headers=tenant, json={
        "client_id": cid, "complaint": "job",
        "lines": [{"line_type": "labour", "description": "Repair", "qty": 1, "unit_price": 100}]})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def test_issue_then_appears_in_list_then_redeem(client, tenant):
    cid = await _client(client, tenant)
    issued = (await client.post("/api/giftcards", json={"value": 60, "client_id": cid}, headers=tenant)).json()
    code = issued["code"]

    # 1) it shows up in the list the drawer reads, scoped to this client
    lst = (await client.get("/api/giftcards", headers=tenant)).json()["items"]
    mine = [g for g in lst if g["client_id"] == cid]
    assert any(g["code"] == code and g["balance"] == 60 for g in mine), mine

    # 2) redeem against a real order → balance drops
    oid = await _order_with_total(client, tenant, cid)
    r = await client.post(f"/api/orders/{oid}/redeem-giftcard", json={"code": code, "amount": 25}, headers=tenant)
    assert r.status_code in (200, 201), r.text
    after = (await client.get("/api/giftcards", headers=tenant)).json()["items"]
    card = next(g for g in after if g["code"] == code)
    assert card["balance"] == 35, card    # 60 − 25


async def test_expired_card_is_rejected_on_redeem(client, tenant):
    cid = await _client(client, tenant, name="Expired Co")
    past = (now_utc() - timedelta(days=1)).isoformat()
    code = (await client.post("/api/giftcards", json={"value": 50, "client_id": cid, "expires_at": past}, headers=tenant)).json()["code"]
    oid = await _order_with_total(client, tenant, cid)
    r = await client.post(f"/api/orders/{oid}/redeem-giftcard", json={"code": code}, headers=tenant)
    assert r.status_code == 400 and "expired" in r.text.lower(), r.text
