"""Dropped jobs (Orderry "Dropped off" workflow).

- Moving a job to a dropped-group status is not blocked by the "finish the work" close gates.
- The unpaid balance is written off as a recorded adjustment, so the job stops showing as owed.
  Reopening puts the balance back.
- Dropped jobs are left out of the order profit reports.
"""
import pytest
from sqlalchemy import select

from backend.models import Client, ClientLedgerEntry, Order, OrderChecklistItem, OrderLine


async def _job(client, h, total=100):
    cid = (await client.post("/api/clients", json={"name": "Dana", "phone": "+15550111"}, headers=h)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid}, headers=h)).json()["id"]
    r = await client.post(f"/api/orders/{oid}/lines", json={
        "line_type": "labour", "description": "Fix", "qty": 1, "unit_price": total}, headers=h)
    assert r.status_code in (200, 201), r.text
    return cid, oid


async def _pay(client, h, oid, amount):
    await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"}, headers=h)
    accts = (await client.get("/api/cash/accounts", headers=h)).json()["items"]
    r = await client.post(f"/api/cash/orders/{oid}/pay",
                          json={"method": "cash", "account_id": accts[0]["id"], "amount": amount}, headers=h)
    assert r.status_code in (200, 201), r.text


async def _status(client, h, oid, status):
    body = {"status": status}
    if status == "dropped":
        body["reason"] = "Customer didn't come back"   # dropping a job needs a reason
    return await client.patch(f"/api/orders/{oid}/status", json=body, headers=h)


async def _remaining(client, h, oid):
    return (await client.get(f"/api/cash/orders/{oid}/payment-context", headers=h)).json()


@pytest.mark.asyncio
async def test_drop_skips_close_gates_but_close_still_enforces_them(client, tenant, db):
    _, oid = await _job(client, tenant)
    # unfinished required checklist item + a part still waiting for stock
    org_id = (await db.execute(select(Order.org_id).where(Order.id == oid))).scalar_one()
    db.add(OrderChecklistItem(org_id=org_id, order_id=oid, label="Test screen", required=True, done=False))
    line = (await db.execute(select(OrderLine).where(OrderLine.order_id == oid))).scalars().first()
    line.pending_stock = 1
    await db.commit()

    r = await _status(client, tenant, oid, "done")
    assert r.status_code == 409, r.text            # finishing the work still needs both

    r = await _status(client, tenant, oid, "dropped")
    assert r.status_code == 200, r.text            # giving up on the job doesn't
    assert r.json()["is_closing"] is True


@pytest.mark.asyncio
async def test_drop_writes_off_unpaid_balance_and_reopen_reverses_it(client, tenant, db):
    cid, oid = await _job(client, tenant, 100)
    await _pay(client, tenant, oid, 30)

    r = await _status(client, tenant, oid, "dropped")
    assert r.status_code == 200, r.text

    o = (await client.get(f"/api/orders/{oid}", headers=tenant)).json()
    assert o["written_off"] == pytest.approx(70)
    assert o["payment_due"] is False        # no "Due" badge, even with the deposit's invoice open
    ctx = await _remaining(client, tenant, oid)
    assert ctx["remaining"] == 0 and ctx["written_off"] == pytest.approx(70)

    # Preview: workstream B records the write-off by lowering the job's charge in the client
    # ledger (not a separate "write_off" line); either way the customer owes nothing.
    c = (await db.execute(select(Client).where(Client.id == cid))).scalar_one()
    await db.refresh(c)
    assert float(c.balance) == pytest.approx(0)

    # dropping again (e.g. re-saving the status) never writes off twice
    r = await _status(client, tenant, oid, "dropped")
    await db.refresh(c)
    assert float(c.balance) == pytest.approx(0)

    # reopen: the balance is owed again (on the job and in the customer's ledger)
    r = await _status(client, tenant, oid, "in_repair")
    assert r.status_code == 200, r.text
    ctx = await _remaining(client, tenant, oid)
    assert ctx["remaining"] == pytest.approx(70) and ctx["written_off"] == 0
    assert (await client.get(f"/api/orders/{oid}", headers=tenant)).json()["payment_due"] is True
    # An open job isn't charged to the customer's ledger until it closes, so after reopening the
    # 30 paid shows as credit again, exactly as before the drop.
    await db.refresh(c)
    assert float(c.balance) == pytest.approx(-30)


@pytest.mark.asyncio
async def test_dropped_then_completed_is_owed_in_full(client, tenant, db):
    _, oid = await _job(client, tenant, 80)
    await _status(client, tenant, oid, "dropped")
    r = await _status(client, tenant, oid, "done")    # customer came back after all
    assert r.status_code == 200, r.text
    ctx = await _remaining(client, tenant, oid)
    assert ctx["remaining"] == pytest.approx(80) and ctx["written_off"] == 0


@pytest.mark.asyncio
async def test_fully_paid_drop_writes_nothing_off(client, tenant, db):
    cid, oid = await _job(client, tenant, 50)
    await _pay(client, tenant, oid, 50)
    await _status(client, tenant, oid, "dropped")
    o = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    assert float(o.written_off or 0) == 0


@pytest.mark.asyncio
async def test_dropped_jobs_left_out_of_profit_reports(client, tenant):
    _, kept = await _job(client, tenant, 100)
    _, dropped = await _job(client, tenant, 60)
    await _status(client, tenant, kept, "done")
    await _status(client, tenant, dropped, "dropped")
    nums = {oid: (await client.get(f"/api/orders/{oid}", headers=tenant)).json()["order_number"]
            for oid in (kept, dropped)}

    rep = (await client.get("/api/reports/run/wo_profit", headers=tenant)).json()
    listed = {r[0] for r in rep["rows"]}
    assert nums[kept] in listed and nums[dropped] not in listed

    jp = (await client.get("/api/reports/job-profitability", headers=tenant)).json()
    listed = {r["order_number"] for r in jp["rows"]}
    assert nums[kept] in listed and nums[dropped] not in listed
