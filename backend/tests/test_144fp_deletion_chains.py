"""A3 — deletion dependency chains: never orphan money."""
import pytest


async def _order_paid(client, h):
    r = await client.post("/api/clients", json={"name": "C", "phone": "+15559"}, headers=h)
    cid = r.json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid}, headers=h)).json()["id"]
    await client.post(f"/api/orders/{oid}/lines", json={
        "line_type": "labour", "description": "Fix", "qty": 1, "unit_price": 100}, headers=h)
    await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"}, headers=h)
    aid = (await client.get("/api/cash/accounts", headers=h)).json()["items"][0]["id"]
    r = await client.post(f"/api/cash/orders/{oid}/pay", json={
        "method": "cash", "account_id": aid, "amount": 100}, headers=h)
    assert r.status_code in (200, 201), r.text
    return oid, aid


@pytest.mark.asyncio
async def test_cannot_delete_order_with_payments_without_refund(client, tenant):
    h = tenant
    oid, _ = await _order_paid(client, h)
    # refund=false → blocked (would orphan the payment)
    r = await client.delete(f"/api/orders/{oid}?refund=false", headers=h)
    assert r.status_code == 409, r.text
    assert "payment" in r.text.lower()
    # refund=true → allowed, and it archives with a refund
    r = await client.delete(f"/api/orders/{oid}?refund=true", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["archived"] is True and r.json()["refunded"] == 100


@pytest.mark.asyncio
async def test_cannot_void_refund_settlement_leg_directly(client, tenant):
    h = tenant
    oid, aid = await _order_paid(client, h)
    # archiving with refund posts a refund transaction (ref_type='refund')
    await client.delete(f"/api/orders/{oid}?refund=true", headers=h)
    txns = (await client.get(f"/api/cash/transactions?account_id={aid}", headers=h)).json()["items"]
    refund_txn = [t for t in txns if t["ref_type"] == "refund"]
    assert refund_txn, "expected a refund settlement transaction"
    r = await client.delete(f"/api/cash/transactions/{refund_txn[0]['id']}", headers=h)
    assert r.status_code == 409 and "refund" in r.text.lower()
