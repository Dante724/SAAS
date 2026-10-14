"""A2 — payment-link lifecycle: created→viewed→paid, generate-once, revoke."""
import pytest


async def _order_with_balance(client, h):
    # create a client + order with a total so there's a balance to collect
    r = await client.post("/api/clients", json={"name": "Payer Co", "phone": "+15550001"}, headers=h)
    cid = r.json()["id"]
    r = await client.post("/api/orders", json={"client_id": cid}, headers=h)
    assert r.status_code in (200, 201), r.text
    oid = r.json()["id"]
    # add a labour line so total > 0
    r = await client.post(f"/api/orders/{oid}/lines", json={
        "line_type": "labour", "description": "Diag", "qty": 1, "unit_price": 100}, headers=h)
    assert r.status_code in (200, 201), r.text
    return oid


@pytest.mark.asyncio
async def test_link_lifecycle_and_generate_once(client, tenant):
    h = tenant
    oid = await _order_with_balance(client, h)

    # create link
    r = await client.post(f"/api/orders/{oid}/payment-link", headers=h)
    assert r.status_code == 201, r.text
    j = r.json()
    iid = j["intent_id"]
    assert j["reused"] is False and j["status"] in ("created", "pending")

    # generate-once: asking again reuses the same link
    r2 = await client.post(f"/api/orders/{oid}/payment-link", headers=h)
    assert r2.status_code == 201 and r2.json()["reused"] is True and r2.json()["intent_id"] == iid

    # public "view" records the open (viewed_at), advancing created→viewed when applicable
    r = await client.post(f"/api/payments/intents/{iid}/view")
    assert r.status_code == 200
    intent = (await client.get(f"/api/payments/intents/{iid}", headers=h)).json()
    assert intent["viewed_at"]

    # simulate payment → Paid, and a payment is created
    r = await client.post(f"/api/payments/intents/{iid}/simulate-pay")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "succeeded" and r.json()["payment_id"]
    intent = (await client.get(f"/api/payments/intents/{iid}", headers=h)).json()
    assert intent["lifecycle"] == "Paid"

    # a paid link cannot be revoked
    r = await client.post(f"/api/payments/intents/{iid}/revoke", headers=h)
    assert r.status_code == 400

    # since the paid link is terminal, a new request mints a fresh link
    r = await client.post(f"/api/orders/{oid}/payment-link", headers=h)
    # balance may now be zero (fully paid) → 400; or a fresh link if still owing
    assert r.status_code in (201, 400)


@pytest.mark.asyncio
async def test_revoke_and_regenerate(client, tenant):
    h = tenant
    oid = await _order_with_balance(client, h)
    r = await client.post(f"/api/orders/{oid}/payment-link", headers=h)
    iid = r.json()["intent_id"]

    # revoke the unpaid link
    r = await client.post(f"/api/payments/intents/{iid}/revoke", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "canceled"

    # a revoked link is not reused — next request mints a new one
    r = await client.post(f"/api/orders/{oid}/payment-link", headers=h)
    assert r.status_code == 201 and r.json()["intent_id"] != iid and r.json()["reused"] is False

    # regenerate=true revokes the current live one and issues another
    first = r.json()["intent_id"]
    r = await client.post(f"/api/orders/{oid}/payment-link?regenerate=true", headers=h)
    assert r.status_code == 201 and r.json()["intent_id"] != first
    old = (await client.get(f"/api/payments/intents/{first}", headers=h)).json()
    assert old["status"] == "canceled"
