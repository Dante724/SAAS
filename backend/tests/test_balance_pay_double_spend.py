"""Hardening council: client credit paid any number of open jobs and invoices.

"Pay from balance" checked available_credit but posted nothing that spent the credit until the
job closed, so 100 of credit paid job A, then job B, then any invoice. A balance-pay now charges
the job's share to the ledger up front (the close nets it, so nothing is billed twice) and moves
the balance by one guarded UPDATE, so a concurrent pair can't both spend the same credit either.
"""
import asyncio

import pytest

LINE = {"line_type": "labour", "kind": "custom", "description": "Work", "unit_price": 100, "qty": 1}


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _till(client, h):
    r = await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _credit_client(client, h, amount=100):
    cid = (await client.post("/api/clients", json={"name": "Credit"}, headers=h)).json()["id"]
    r = await client.post(f"/api/clients/{cid}/balance", json={"kind": "advance", "amount": amount}, headers=h)
    assert r.status_code == 201, r.text
    return cid


async def _job(client, h, cid, price=100):
    loc = await _loc(client, h)
    r = await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x",
                                               "is_estimate": False, "lines": []}, headers=h)
    assert r.status_code in (200, 201), r.text
    oid = r.json()["id"]
    r = await client.post(f"/api/orders/{oid}/lines", json={**LINE, "unit_price": price}, headers=h)
    assert r.status_code in (200, 201), r.text
    return oid


async def _pay_balance(client, h, oid, amount=None):
    body = {"method": "balance"} if amount is None else {"method": "balance", "amount": amount}
    return await client.post(f"/api/cash/orders/{oid}/pay", json=body, headers=h)


async def _close(client, h, oid):
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": "done"}, headers=h)
    assert r.status_code == 200, r.text


async def _balance(client, h, cid):
    b = (await client.get(f"/api/clients/{cid}/balance", headers=h)).json()
    assert b["balance"] == pytest.approx(sum(e["amount"] for e in b["entries"])), b   # mirror == ledger
    return b["balance"]


@pytest.mark.asyncio
async def test_client_credit_cannot_pay_two_open_jobs(client, tenant):
    cid = await _credit_client(client, tenant)
    a, b = await _job(client, tenant, cid), await _job(client, tenant, cid)
    ra = await _pay_balance(client, tenant, a)
    assert ra.status_code == 200, ra.text
    assert await _balance(client, tenant, cid) == pytest.approx(0)      # the credit is spent
    rb = await _pay_balance(client, tenant, b)
    assert rb.status_code == 400, f"100 of credit paid a second 100 job: {rb.text}"
    assert "credit" in rb.json()["detail"].lower()


@pytest.mark.asyncio
async def test_client_credit_cannot_pay_two_invoices(client, tenant):
    cid = await _credit_client(client, tenant)
    invs = []
    for _ in range(2):
        r = await client.post("/api/finance/invoices", json={
            "client_id": cid, "lines": [{"description": "Repair", "qty": 1, "unit_price": 100}]}, headers=tenant)
        assert r.status_code == 201, r.text
        invs.append(r.json()["id"])
    r1 = await client.post(f"/api/finance/invoices/{invs[0]}/pay", json={"method": "balance", "amount": 100}, headers=tenant)
    assert r1.status_code == 200, r1.text
    assert await _balance(client, tenant, cid) == pytest.approx(0)
    r2 = await client.post(f"/api/finance/invoices/{invs[1]}/pay", json={"method": "balance", "amount": 100}, headers=tenant)
    assert r2.status_code == 400, f"100 of credit paid 200 of invoices: {r2.text}"


@pytest.mark.asyncio
async def test_concurrent_balance_pays_spend_the_credit_once(client, tenant):
    cid = await _credit_client(client, tenant)
    a, b = await _job(client, tenant, cid), await _job(client, tenant, cid)
    ra, rb = await asyncio.gather(_pay_balance(client, tenant, a), _pay_balance(client, tenant, b))
    assert sorted([ra.status_code, rb.status_code]) == [200, 400], (ra.text, rb.text)
    assert await _balance(client, tenant, cid) == pytest.approx(0)


@pytest.mark.asyncio
async def test_closing_a_job_paid_from_credit_charges_it_once(client, tenant):
    cid = await _credit_client(client, tenant)
    oid = await _job(client, tenant, cid)
    assert (await _pay_balance(client, tenant, oid)).status_code == 200
    await _close(client, tenant, oid)
    assert await _balance(client, tenant, cid) == pytest.approx(0)      # not billed a second time
    ctx = (await client.get(f"/api/cash/orders/{oid}/payment-context", headers=tenant)).json()
    assert ctx["remaining"] == 0


@pytest.mark.asyncio
async def test_part_credit_part_cash_nets_like_before(client, tenant):
    """60 from credit and 40 cash on a 100 job: the client keeps the 40 of credit they didn't use."""
    acc = await _till(client, tenant)
    cid = await _credit_client(client, tenant)
    oid = await _job(client, tenant, cid)
    assert (await _pay_balance(client, tenant, oid, 60)).status_code == 200
    assert await _balance(client, tenant, cid) == pytest.approx(-40)
    r = await client.post(f"/api/cash/orders/{oid}/pay", json={"method": "cash", "account_id": acc, "amount": 40}, headers=tenant)
    assert r.status_code == 200, r.text
    await _close(client, tenant, oid)
    assert await _balance(client, tenant, cid) == pytest.approx(-40)


@pytest.mark.asyncio
async def test_credit_left_after_a_balance_pay_still_pays_another_job(client, tenant):
    cid = await _credit_client(client, tenant, 150)
    a, b = await _job(client, tenant, cid), await _job(client, tenant, cid, 50)
    assert (await _pay_balance(client, tenant, a)).status_code == 200
    assert (await _pay_balance(client, tenant, b)).status_code == 200
    assert await _balance(client, tenant, cid) == pytest.approx(0)


async def _move(client, h, oid, status):
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": status}, headers=h)
    assert r.status_code == 200, r.text


def _no_credit_left(r):
    assert r.status_code == 400, f"the same credit paid a second job: {r.text}"
    assert "only 0.00 of credit" in r.json()["detail"], r.text      # available credit is 0


@pytest.mark.asyncio
async def test_moving_a_credit_paid_job_on_keeps_the_credit_spent(client, tenant):
    """Review: the status resync set a non-closing job's charge target to 0, which reversed the
    spend and handed the credit back while job A stayed paid from it."""
    cid = await _credit_client(client, tenant)
    a, b = await _job(client, tenant, cid), await _job(client, tenant, cid)
    assert (await _pay_balance(client, tenant, a)).status_code == 200
    await _move(client, tenant, a, "in_repair")
    assert await _balance(client, tenant, cid) == pytest.approx(0)
    _no_credit_left(await _pay_balance(client, tenant, b))
    await _close(client, tenant, a)                                     # and the close still bills once
    assert await _balance(client, tenant, cid) == pytest.approx(0)


@pytest.mark.asyncio
async def test_reopening_a_job_paid_from_credit_after_close_keeps_the_credit_spent(client, tenant):
    cid = await _credit_client(client, tenant)
    a, b = await _job(client, tenant, cid), await _job(client, tenant, cid)
    await _close(client, tenant, a)
    assert (await _pay_balance(client, tenant, a)).status_code == 200
    await _move(client, tenant, a, "in_repair")
    assert await _balance(client, tenant, cid) == pytest.approx(0)
    _no_credit_left(await _pay_balance(client, tenant, b))


@pytest.mark.asyncio
async def test_archiving_a_job_paid_from_credit_gives_the_credit_back(client, tenant):
    cid = await _credit_client(client, tenant)
    a, b = await _job(client, tenant, cid), await _job(client, tenant, cid)
    assert (await _pay_balance(client, tenant, a)).status_code == 200
    r = await client.post(f"/api/orders/{a}/archive", json={"reason": "Customer changed mind"}, headers=tenant)
    assert r.status_code == 200, r.text
    assert await _balance(client, tenant, cid) == pytest.approx(-100)
    assert (await _pay_balance(client, tenant, b)).status_code == 200


@pytest.mark.asyncio
async def test_money_taken_on_another_open_job_is_not_credit(client, tenant):
    """Review: available_credit netted only the target job, so a cash payment held on another
    open job counted as free credit and paid this one."""
    acc = await _till(client, tenant)
    cid = (await client.post("/api/clients", json={"name": "No credit"}, headers=tenant)).json()["id"]
    k, j = await _job(client, tenant, cid), await _job(client, tenant, cid)
    r = await client.post(f"/api/cash/orders/{k}/pay", json={"method": "cash", "account_id": acc, "amount": 100},
                          headers=tenant)
    assert r.status_code == 200, r.text
    _no_credit_left(await _pay_balance(client, tenant, j))
    r = await client.post("/api/finance/invoices", json={
        "client_id": cid, "lines": [{"description": "Repair", "qty": 1, "unit_price": 100}]}, headers=tenant)
    assert r.status_code == 201, r.text
    r = await client.post(f"/api/finance/invoices/{r.json()['id']}/pay", json={"method": "balance", "amount": 100},
                          headers=tenant)
    _no_credit_left(r)
    await _close(client, tenant, k)                 # once K is billed, its money is no longer held for it
    assert await _balance(client, tenant, cid) == pytest.approx(0)


@pytest.mark.asyncio
async def test_closed_job_cannot_be_paid_from_credit_twice_over(client, tenant):
    cid = await _credit_client(client, tenant, 150)
    oid = await _job(client, tenant, cid, 200)
    await _close(client, tenant, oid)
    assert (await _pay_balance(client, tenant, oid, 150)).status_code == 200
    _no_credit_left(await _pay_balance(client, tenant, oid, 50))
    assert await _balance(client, tenant, cid) == pytest.approx(50)     # still owes the 50


@pytest.mark.asyncio
async def test_crediting_back_a_jobless_invoice_paid_from_credit(client, tenant):
    """A job-less invoice has no refund route: a credit note gives the credit back, cancelling the
    note takes it again, and the ledger and the balance agree at every step."""
    cid = await _credit_client(client, tenant)
    invs = []
    for _ in range(2):
        r = await client.post("/api/finance/invoices", json={
            "client_id": cid, "lines": [{"description": "Repair", "qty": 1, "unit_price": 100}]}, headers=tenant)
        assert r.status_code == 201, r.text
        invs.append(r.json()["id"])
    r = await client.post(f"/api/finance/invoices/{invs[0]}/pay", json={"method": "balance", "amount": 100},
                          headers=tenant)
    assert r.status_code == 200, r.text
    assert await _balance(client, tenant, cid) == pytest.approx(0)
    r = await client.post("/api/finance/credit-notes", headers=tenant, json={
        "client_id": cid, "invoice_id": invs[0], "reason": "Goodwill",
        "lines": [{"description": "Repair", "qty": 1, "unit_price": 100}]})
    assert r.status_code == 201, r.text
    cn = r.json()["id"]
    assert (await client.post(f"/api/finance/credit-notes/{cn}/issue", headers=tenant)).status_code == 200
    assert await _balance(client, tenant, cid) == pytest.approx(-100)   # the credit is back
    assert (await client.post(f"/api/finance/credit-notes/{cn}/cancel", headers=tenant)).status_code == 200
    b = (await client.get(f"/api/clients/{cid}/balance", headers=tenant)).json()
    assert b["balance"] == pytest.approx(0), b
    kinds = sorted((e["entry_type"], e["amount"]) for e in b["entries"])
    assert kinds == sorted([("advance", -100), ("charge", 100), ("credit_note", -100),
                            ("credit_note_void", 100)]), kinds
    _no_credit_left(await client.post(f"/api/finance/invoices/{invs[1]}/pay",
                                      json={"method": "balance", "amount": 100}, headers=tenant))
