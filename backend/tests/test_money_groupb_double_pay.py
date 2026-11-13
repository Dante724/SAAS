"""Re-review money N9: a client advance/payout and a supplier payment had no Idempotency-Key and no
double-click guard, so a double tap booked the money twice (FIN-01 pattern now applies)."""
import uuid

from backend.tests.test_p1_money_audit import _acct, _balance, _client


async def test_client_advance_double_click_is_refused_and_key_replays(client, tenant, db):
    till = await _acct(client, tenant, "Till")
    cid = await _client(client, tenant)
    body = {"kind": "advance", "amount": 40, "account_id": till}
    r = await client.post(f"/api/clients/{cid}/balance", json=body, headers=tenant)
    assert r.status_code == 201, r.text
    r = await client.post(f"/api/clients/{cid}/balance", json=body, headers=tenant)
    assert r.status_code == 409, r.text                       # a double click, not a second advance
    assert await _balance(db, till) == 40.0
    # a payout of the same amount is a different movement
    r = await client.post(f"/api/clients/{cid}/balance", json={**body, "kind": "payout"}, headers=tenant)
    assert r.status_code == 201, r.text
    # with a key: a retry gets the first answer back, nothing moves twice
    k = {**tenant, "Idempotency-Key": uuid.uuid4().hex}
    a = await client.post(f"/api/clients/{cid}/balance", json={**body, "amount": 15}, headers=k)
    b = await client.post(f"/api/clients/{cid}/balance", json={**body, "amount": 15}, headers=k)
    assert a.status_code == 201 and b.status_code == 201, (a.text, b.text)
    assert a.json()["id"] == b.json()["id"]
    assert await _balance(db, till) == 15.0


async def test_supplier_payment_double_click_is_refused_and_key_replays(client, tenant, db):
    till = await _acct(client, tenant, "Till")
    r = await client.post("/api/cash/transactions", json={"account_id": till, "txn_type": "income",
                                                          "amount": 500}, headers=tenant)
    assert r.status_code == 201, r.text
    r = await client.post("/api/suppliers", json={"name": f"Parts {uuid.uuid4().hex[:4]}"}, headers=tenant)
    assert r.status_code in (200, 201), r.text
    sid = r.json()["id"]
    body = {"amount": 100, "account_id": till}
    assert (await client.post(f"/api/suppliers/{sid}/pay", json=body, headers=tenant)).status_code == 200
    r = await client.post(f"/api/suppliers/{sid}/pay", json=body, headers=tenant)
    assert r.status_code == 409, r.text
    assert await _balance(db, till) == 400.0
    k = {**tenant, "Idempotency-Key": uuid.uuid4().hex}
    a = await client.post(f"/api/suppliers/{sid}/pay", json={**body, "amount": 30}, headers=k)
    b = await client.post(f"/api/suppliers/{sid}/pay", json={**body, "amount": 30}, headers=k)
    assert a.status_code == 200 and b.status_code == 200, (a.text, b.text)
    assert await _balance(db, till) == 370.0
