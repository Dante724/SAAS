"""Audit FIN-01 (2026-11-12): the two refund endpoints had no idempotency or double-click guard.

The order lock only stopped a refund above what was paid; a repeated partial refund under that cap
went through, even when the client sent the same Idempotency-Key, so the cash left the till twice
(and commissions were clawed back twice, once per refund id). Now:
- the same Idempotency-Key gets the first answer back, and nothing is booked again;
- the same key with a different body is refused (422);
- without a key, the same refund (job, cashbox, amount) within a few seconds is refused (409),
  as a double-clicked payment already is;
- the "refundable remaining" cap is still read under the job's lock, so a second refund that
  would exceed what was paid is refused.
"""
import uuid

from sqlalchemy import func, select

from backend.models import Refund


async def _setup(client, h, *, price=100):
    till = (await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"},
                              headers=h)).json()["id"]
    cid = (await client.post("/api/clients", json={"name": "Refund Client",
                                                   "phone": "+1555" + uuid.uuid4().hex[:7]}, headers=h)).json()["id"]
    r = await client.post("/api/orders", headers=h, json={
        "client_id": cid, "complaint": "job",
        "lines": [{"line_type": "labour", "description": "Repair", "qty": 1, "unit_price": price}]})
    assert r.status_code in (200, 201), r.text
    return till, cid, r.json()["id"]


async def _till(client, h, aid):
    items = (await client.get("/api/cash/accounts", headers=h)).json()["items"]
    return [x for x in items if x["id"] == aid][0]["balance"] or 0.0


async def _refunds(db, oid):
    return (await db.execute(select(func.count()).select_from(Refund).where(Refund.order_id == oid))).scalar()


async def test_refund_with_same_key_books_once(client, tenant, db):
    h = tenant
    till, _, oid = await _setup(client, h)
    r = await client.post(f"/api/cash/orders/{oid}/pay", json={"method": "cash", "account_id": till, "amount": 100},
                          headers=h)
    assert r.status_code == 200, r.text
    hk = {**h, "Idempotency-Key": "refund-key-1"}
    body = {"account_id": till, "amount": 30, "order_id": oid, "reason": "goodwill"}
    r1 = await client.post("/api/returns/refunds", json=body, headers=hk)
    r2 = await client.post("/api/returns/refunds", json=body, headers=hk)
    assert r1.status_code == 201, r1.text
    assert r2.status_code == 201, r2.text
    assert r2.json()["id"] == r1.json()["id"]          # the first answer, replayed
    assert await _till(client, h, till) == 70.0        # 100 in, 30 out once
    assert await _refunds(db, oid) == 1
    # the same key reused for a different refund is refused, not replayed
    r3 = await client.post("/api/returns/refunds", json={**body, "amount": 10}, headers=hk)
    assert r3.status_code == 422, r3.text


async def test_refund_double_click_without_key_is_refused(client, tenant, db):
    h = tenant
    till, _, oid = await _setup(client, h)
    await client.post(f"/api/cash/orders/{oid}/pay", json={"method": "cash", "account_id": till, "amount": 100},
                      headers=h)
    body = {"account_id": till, "amount": 30, "order_id": oid, "reason": "goodwill"}
    r1 = await client.post("/api/returns/refunds", json=body, headers=h)
    r2 = await client.post("/api/returns/refunds", json=body, headers=h)
    assert r1.status_code == 201, r1.text
    assert r2.status_code == 409, r2.text
    assert await _till(client, h, till) == 70.0
    assert await _refunds(db, oid) == 1
    # a different amount is a different refund and still goes through
    r3 = await client.post("/api/returns/refunds", json={**body, "amount": 20}, headers=h)
    assert r3.status_code == 201, r3.text
    assert await _till(client, h, till) == 50.0


async def test_second_refund_over_what_was_paid_is_refused(client, tenant):
    h = tenant
    till, _, oid = await _setup(client, h)
    await client.post(f"/api/cash/orders/{oid}/pay", json={"method": "cash", "account_id": till, "amount": 100},
                      headers=h)
    body = {"account_id": till, "amount": 60, "order_id": oid}
    r1 = await client.post("/api/returns/refunds", json=body, headers={**h, "Idempotency-Key": "a-1"})
    r2 = await client.post("/api/returns/refunds", json=body, headers={**h, "Idempotency-Key": "a-2"})
    assert r1.status_code == 201, r1.text
    assert r2.status_code == 400, r2.text              # only 40 left to refund
    assert await _till(client, h, till) == 40.0


async def test_deposit_refund_with_same_key_books_once(client, tenant, db):
    h = tenant
    till, _, oid = await _setup(client, h)
    r = await client.post(f"/api/cash/orders/{oid}/prepay", json={"amount": 80, "account_id": till}, headers=h)
    assert r.status_code == 201, r.text
    hk = {**h, "Idempotency-Key": "dep-refund-1"}
    body = {"amount": 40, "account_id": till}
    r1 = await client.post(f"/api/cash/orders/{oid}/refund", json=body, headers=hk)
    r2 = await client.post(f"/api/cash/orders/{oid}/refund", json=body, headers=hk)
    assert r1.status_code == 201, r1.text
    assert r2.status_code == 201, r2.text
    assert r2.json()["id"] == r1.json()["id"]
    assert await _till(client, h, till) == 40.0
    assert await _refunds(db, oid) == 1


async def test_deposit_refund_double_click_without_key_is_refused(client, tenant, db):
    h = tenant
    till, _, oid = await _setup(client, h)
    await client.post(f"/api/cash/orders/{oid}/prepay", json={"amount": 80, "account_id": till}, headers=h)
    body = {"amount": 40, "account_id": till}
    r1 = await client.post(f"/api/cash/orders/{oid}/refund", json=body, headers=h)
    r2 = await client.post(f"/api/cash/orders/{oid}/refund", json=body, headers=h)
    assert r1.status_code == 201, r1.text
    assert r2.status_code == 409, r2.text
    assert await _till(client, h, till) == 40.0
    assert await _refunds(db, oid) == 1
