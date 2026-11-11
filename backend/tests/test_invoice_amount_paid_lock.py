"""Hardening council: two jobs on one invoice paid at the same time lost one payment.

`_credit` (and the other places that move Invoice.amount_paid) did `inv.amount_paid = amount_paid
+ x` — read in Python, written at the next flush — while apply_order_payment locks only the job.
Two jobs billed on one invoice, paid together into different cashboxes, both read amount_paid 0:
the invoice ended at 100 instead of 160 and stayed Partial (3/3 live runs). amount_paid now moves
with the atomic add_to_column UPDATE and the status is settled from the refreshed value.
"""
import asyncio
import os

import pytest


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _till(client, h, name, option):
    r = await client.post("/api/cash/accounts", json={"name": name, "payment_option": option}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _job(client, h, loc, price):
    r = await client.post("/api/orders", json={"location_id": loc, "complaint": "x", "is_estimate": False,
                                               "lines": []}, headers=h)
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


@pytest.mark.asyncio
async def test_two_jobs_on_one_invoice_paid_together(client, tenant):
    loc = await _loc(client, tenant)
    cash = await _till(client, tenant, "Till", "cash")
    card = await _till(client, tenant, "Card", "card")
    a, b = await _job(client, tenant, loc, 100), await _job(client, tenant, loc, 60)
    iid = await _shared_invoice(client, tenant, a, b)

    ra, rb = await asyncio.gather(
        client.post(f"/api/cash/orders/{a}/pay", json={"method": "cash", "account_id": cash, "amount": 100}, headers=tenant),
        client.post(f"/api/cash/orders/{b}/pay", json={"method": "card", "account_id": card, "amount": 60}, headers=tenant))
    assert ra.status_code == 200, ra.text
    assert rb.status_code == 200, rb.text

    inv = (await client.get(f"/api/finance/invoices/{iid}", headers=tenant)).json()
    assert inv["amount_paid"] == pytest.approx(160), inv
    assert inv["status"] == "paid", inv


@pytest.mark.asyncio
async def test_two_jobs_on_one_invoice_paid_in_turn(client, tenant):
    """The same without the race: each job's share lands and the invoice settles."""
    loc = await _loc(client, tenant)
    cash = await _till(client, tenant, "Till", "cash")
    a, b = await _job(client, tenant, loc, 100), await _job(client, tenant, loc, 60)
    iid = await _shared_invoice(client, tenant, a, b)
    for oid, amt in ((a, 100), (b, 60)):
        r = await client.post(f"/api/cash/orders/{oid}/pay", json={"method": "cash", "account_id": cash, "amount": amt}, headers=tenant)
        assert r.status_code == 200, r.text
    inv = (await client.get(f"/api/finance/invoices/{iid}", headers=tenant)).json()
    assert inv["amount_paid"] == pytest.approx(160) and inv["status"] == "paid", inv


# --- Lock order (independent review of #111) ------------------------------------------------
# Postgres only: the two requests must run in two transactions that can wait on each other's row
# locks. SQLite serialises writes and never deadlocks.
_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
pg_only = pytest.mark.skipif(not _URL.startswith("postgresql"), reason="row locks are Postgres only")


async def _client_job(client, h, loc, price):
    cid = (await client.post("/api/clients", json={"name": "C", "phone": "+15559"}, headers=h)).json()["id"]
    r = await client.post("/api/orders", json={"location_id": loc, "client_id": cid, "complaint": "x",
                                               "is_estimate": False, "lines": []}, headers=h)
    assert r.status_code in (200, 201), r.text
    oid = r.json()["id"]
    r = await client.post(f"/api/orders/{oid}/lines", json={
        "line_type": "labour", "kind": "custom", "description": "Work", "unit_price": price, "qty": 1}, headers=h)
    assert r.status_code in (200, 201), r.text
    return cid, oid


async def _invoice(client, h, oid):
    r = await client.post("/api/finance/invoices", json={"order_id": oid}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pg_only
@pytest.mark.asyncio
async def test_pay_and_prepay_on_one_invoiced_job_together(client, tenant):
    """Pre-locking the job's invoices before the job (Invoice -> Order) in the till payment
    deadlocked against every other money path, which takes Order -> Invoice: a till payment and a
    prepayment on the same invoiced job at once gave PAY a 500 (DeadlockDetectedError). The job
    is now locked FOR NO KEY UPDATE first, like everywhere else: both land, the invoice settles."""
    loc = await _loc(client, tenant)
    cash = await _till(client, tenant, "Till", "cash")
    _cid, oid = await _client_job(client, tenant, loc, 100)
    iid = await _invoice(client, tenant, oid)

    pay, pre = await asyncio.gather(
        client.post(f"/api/cash/orders/{oid}/pay", json={"method": "cash", "account_id": cash, "amount": 60}, headers=tenant),
        client.post(f"/api/cash/orders/{oid}/prepay", json={"method": "cash", "account_id": cash, "amount": 40}, headers=tenant))
    assert pay.status_code == 200, pay.text
    assert pre.status_code == 201, pre.text

    inv = (await client.get(f"/api/finance/invoices/{iid}", headers=tenant)).json()
    assert inv["amount_paid"] == pytest.approx(100), inv
    assert inv["status"] == "paid", inv
    o = (await client.get(f"/api/orders/{oid}", headers=tenant)).json()
    assert o.get("prepayment_total") == pytest.approx(40), o


@pg_only
@pytest.mark.asyncio
async def test_prepay_and_invoice_payment_together(client, tenant):
    """Invoices › Record payment locks Invoice -> Client; a prepayment posted the client ledger
    (Client) before putting the money on the invoice (Invoice). The two at once deadlocked. The
    prepayment now lands on the invoice first, then posts the ledger: Invoice -> Client both ways."""
    loc = await _loc(client, tenant)
    cash = await _till(client, tenant, "Till", "cash")
    cid, oid = await _client_job(client, tenant, loc, 100)
    iid = await _invoice(client, tenant, oid)

    pre, pay = await asyncio.gather(
        client.post(f"/api/cash/orders/{oid}/prepay", json={"method": "cash", "account_id": cash, "amount": 40}, headers=tenant),
        client.post(f"/api/finance/invoices/{iid}/pay", json={"method": "cash", "account_id": cash, "amount": 60}, headers=tenant))
    assert pre.status_code == 201, pre.text
    assert pay.status_code == 200, pay.text

    inv = (await client.get(f"/api/finance/invoices/{iid}", headers=tenant)).json()
    assert inv["amount_paid"] == pytest.approx(100), inv
    assert inv["status"] == "paid", inv
    c = (await client.get(f"/api/clients/{cid}", headers=tenant)).json()
    # Both receipts reach the ledger once: 100 received on a job that isn't closed yet (its charge
    # posts when it closes), the same -100 the two payments leave when made one after the other.
    assert c["balance"] == pytest.approx(-100), c["balance"]
