"""Hardening council: money could be taken on a deleted (archived) job.

Archiving a job refunds what it had taken, but pay / prepay / pay-link / invoice paths loaded
the Order without looking at `archived`: paying 100 and prepaying 50 on an archived job
answered 200/201, put 150 in the till and left the client with phantom credit. Every path that
takes money against a job, or bills it, now answers 409 "This job was deleted. Restore it first."
"""
import pytest

LINE = {"line_type": "labour", "kind": "custom", "description": "Work", "unit_price": 100, "qty": 1}


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _till(client, h):
    r = await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _job(client, h, cid=None):
    loc = await _loc(client, h)
    cid = cid or (await client.post("/api/clients", json={"name": "Cust"}, headers=h)).json()["id"]
    r = await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x",
                                               "is_estimate": False, "lines": []}, headers=h)
    assert r.status_code in (200, 201), r.text
    oid = r.json()["id"]
    r = await client.post(f"/api/orders/{oid}/lines", json=LINE, headers=h)
    assert r.status_code in (200, 201), r.text
    return oid, cid


async def _archive(client, h, oid):
    r = await client.post(f"/api/orders/{oid}/archive", json={"reason": "cancelled", "refund": True}, headers=h)
    assert r.status_code == 200, r.text


async def _balance(client, h, acc):
    return next(a for a in (await client.get("/api/cash/accounts", headers=h)).json()["items"]
                if a["id"] == acc)["balance"]


@pytest.mark.asyncio
async def test_archived_job_cannot_take_money(client, tenant):
    acc = await _till(client, tenant)
    oid, cid = await _job(client, tenant)
    await _archive(client, tenant, oid)
    pay = await client.post(f"/api/cash/orders/{oid}/pay",
                            json={"method": "cash", "account_id": acc, "amount": 100}, headers=tenant)
    pre = await client.post(f"/api/cash/orders/{oid}/prepay",
                            json={"method": "cash", "account_id": acc, "amount": 50}, headers=tenant)
    assert pay.status_code == 409, f"paid an archived job: {pay.text}"
    assert pre.status_code == 409, f"prepaid an archived job: {pre.text}"
    assert "deleted" in pay.json()["detail"]
    assert await _balance(client, tenant, acc) == 0
    bal = (await client.get(f"/api/clients/{cid}/balance", headers=tenant)).json()
    assert bal["balance"] == pytest.approx(0), bal


@pytest.mark.asyncio
async def test_archived_job_cannot_be_invoiced_or_linked(client, tenant):
    oid, cid = await _job(client, tenant)
    other, _ = await _job(client, tenant, cid)
    r = await client.post("/api/finance/invoices", json={"order_id": other, "client_id": cid}, headers=tenant)
    assert r.status_code == 201, r.text
    shared = r.json()["id"]
    await _archive(client, tenant, oid)

    r = await client.post("/api/finance/invoices", json={"order_id": oid, "client_id": cid}, headers=tenant)
    assert r.status_code == 409, r.text
    r = await client.post(f"/api/cash/orders/{oid}/invoices", json={}, headers=tenant)
    assert r.status_code == 409, r.text
    r = await client.post(f"/api/finance/invoices/{shared}/orders", json={"order_id": oid}, headers=tenant)
    assert r.status_code == 409, r.text
    r = await client.post("/api/payments/checkout", json={"order_id": oid, "amount": 50}, headers=tenant)
    assert r.status_code == 409, r.text
    # the job it still bills is untouched
    inv = (await client.get(f"/api/finance/invoices/{shared}", headers=tenant)).json()
    assert inv["status"] != "cancelled"


@pytest.mark.asyncio
async def test_restored_job_takes_money_again(client, tenant):
    acc = await _till(client, tenant)
    oid, _ = await _job(client, tenant)
    await _archive(client, tenant, oid)
    r = await client.post(f"/api/orders/{oid}/restore", headers=tenant)
    assert r.status_code == 200, r.text
    pay = await client.post(f"/api/cash/orders/{oid}/pay",
                            json={"method": "cash", "account_id": acc, "amount": 100}, headers=tenant)
    assert pay.status_code == 200, pay.text
    assert await _balance(client, tenant, acc) == 100
