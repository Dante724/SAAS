"""Hardening council M22 — every money-in path locks Invoice → Order → Client, the order
finance._record_payment (Invoices › Record payment) already follows. A prepayment took the job,
then the client's ledger row, then wrote the shared invoice; a payment recorded on that invoice
held the invoice and then wanted the client row: each waited on the other and Postgres killed one
of them (500). PR #111 fixed cash._pay_order; this covers _prepay_order, the card-reader and
pay-link settle paths and credit notes, by locking the job's live invoices (ids sorted) first."""
import asyncio
import os

import pytest

_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
pg_only = pytest.mark.skipif(not _URL.startswith("postgresql"), reason="row locks deadlock on Postgres only")


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _till(client, h, name, option="cash"):
    r = await client.post("/api/cash/accounts", json={"name": name, "payment_option": option}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _job(client, h, loc, cid, price):
    r = await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x",
                                               "is_estimate": False, "lines": []}, headers=h)
    assert r.status_code in (200, 201), r.text
    oid = r.json()["id"]
    r = await client.post(f"/api/orders/{oid}/lines", json={
        "line_type": "labour", "kind": "custom", "description": "Work", "unit_price": price, "qty": 1}, headers=h)
    assert r.status_code in (200, 201), r.text
    return oid


async def _shared_invoice(client, h, a, b):
    r = await client.post("/api/finance/invoices", json={"order_id": a}, headers=h)
    assert r.status_code == 201, r.text
    iid = r.json()["id"]
    r = await client.post(f"/api/finance/invoices/{iid}/orders", json={"order_id": b}, headers=h)
    assert r.status_code == 200, r.text
    inv = (await client.get(f"/api/finance/invoices/{iid}", headers=h)).json()
    assert inv["total"] == pytest.approx(160), inv
    return iid


async def _setup(client, h):
    loc = await _loc(client, h)
    cid = (await client.post("/api/clients", json={"name": "Shared"}, headers=h)).json()["id"]
    a, b = await _job(client, h, loc, cid, 100), await _job(client, h, loc, cid, 60)
    return a, b, await _shared_invoice(client, h, a, b)


@pg_only
@pytest.mark.asyncio
async def test_prepay_and_invoice_payment_on_a_shared_invoice_do_not_deadlock(client, tenant):
    """50 prepaid on job A while 50 is recorded on the invoice of A and B: whichever lands first,
    both post and the invoice shows 100 paid (A's 50 of prepayment, then 50 against its line)."""
    a, b, iid = await _setup(client, tenant)
    till, till2 = await _till(client, tenant, "Till"), await _till(client, tenant, "Till 2")
    rp, ri = await asyncio.gather(
        client.post(f"/api/cash/orders/{a}/prepay", json={"method": "cash", "account_id": till, "amount": 50},
                    headers=tenant),
        client.post(f"/api/finance/invoices/{iid}/pay", json={"method": "cash", "account_id": till2, "amount": 50},
                    headers=tenant))
    assert rp.status_code == 201, rp.text
    assert ri.status_code == 200, ri.text
    inv = (await client.get(f"/api/finance/invoices/{iid}", headers=tenant)).json()
    assert inv["amount_paid"] == pytest.approx(100), inv


@pg_only
@pytest.mark.asyncio
async def test_pay_link_settle_and_invoice_payment_on_a_shared_invoice_do_not_deadlock(client, tenant):
    """A pay link settling on job A (services.payments.mark_succeeded) posted to the till, then
    the job, then the shared invoice; Record payment into the same till held the invoice and
    waited for the till. Both post, 100 paid."""
    a, b, iid = await _setup(client, tenant)
    till = await _till(client, tenant, "Till")
    r = await client.post("/api/payments/checkout", json={"order_id": a, "amount": 50, "account_id": till},
                          headers=tenant)
    assert r.status_code == 201, r.text
    rl, ri = await asyncio.gather(
        client.post(f"/api/payments/intents/{r.json()['id']}/simulate-pay"),
        client.post(f"/api/finance/invoices/{iid}/pay", json={"method": "cash", "account_id": till, "amount": 50},
                    headers=tenant))
    assert rl.status_code == 200 and rl.json()["status"] == "succeeded", rl.text
    assert ri.status_code == 200, ri.text
    inv = (await client.get(f"/api/finance/invoices/{iid}", headers=tenant)).json()
    assert inv["amount_paid"] == pytest.approx(100), inv
