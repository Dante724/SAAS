"""Hardening council: two jobs on one invoice paid at the same time lost one payment.

`_credit` (and the other places that move Invoice.amount_paid) did `inv.amount_paid = amount_paid
+ x` — read in Python, written at the next flush — while apply_order_payment locks only the job.
Two jobs billed on one invoice, paid together into different cashboxes, both read amount_paid 0:
the invoice ended at 100 instead of 160 and stayed Partial (3/3 live runs). amount_paid now moves
with the atomic add_to_column UPDATE and the status is settled from the refreshed value.
"""
import asyncio

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
