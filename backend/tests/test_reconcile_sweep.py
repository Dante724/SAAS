"""Nightly integrity sweep (backend/services/reconcile.py).

Hardening council: H1, H4, H5 and H7 were all "a stored running total drifted from its own
ledger", and nothing would have noticed in production. The sweep re-derives each total from its
ledger and reports every row that disagrees. These tests make real money and stock move through
the API, check the sweep finds nothing, then corrupt one stored total at a time and check the
sweep names it.
"""
import logging

import pytest
from sqlalchemy import select, update

from backend.models import (
    Account,
    Client,
    Invoice,
    Location,
    StockLevel,
    Transaction,
)
from backend.services import reconcile


async def _shop(client, h):
    """A client with a paid, invoiced job and a product with stock: rows in every ledger."""
    loc = (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]
    till = (await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"},
                              headers=h)).json()["id"]
    cid = (await client.post("/api/clients", json={"name": "Sweep"}, headers=h)).json()["id"]
    pid = (await client.post("/api/catalog/products", headers=h, json={
        "name": "Screen", "sku": "SW-1", "cost_price": 10, "sell_price": 50})).json()["id"]
    r = await client.post(f"/api/inventory/parts/{pid}/adjust", headers=h,
                          json={"location_id": loc, "qty": 5, "movement_type": "receive", "unit_cost": 10})
    assert r.status_code == 200, r.text
    r = await client.post("/api/orders", headers=h, json={
        "client_id": cid, "location_id": loc,
        "lines": [{"line_type": "labour", "description": "Fix", "qty": 1, "unit_price": 100}]})
    assert r.status_code == 201, r.text
    oid = r.json()["id"]
    iid = (await client.post("/api/finance/invoices", json={"order_id": oid}, headers=h)).json()["id"]
    r = await client.post(f"/api/finance/invoices/{iid}/pay", headers=h,
                          json={"method": "cash", "account_id": till, "amount": 60})
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/clients/{cid}/advance", headers=h,
                          json={"amount": 25, "account_id": till, "method": "cash"})
    org = (await client.get("/api/auth/me", headers=h)).json()["org_id"]
    return {"org": org, "loc": loc, "till": till, "client": cid, "part": pid, "invoice": iid,
            "advance_ok": r.status_code in (200, 201)}


@pytest.mark.asyncio
async def test_real_money_and_stock_flows_reconcile(client, tenant, db):
    s = await _shop(client, tenant)
    assert await reconcile.find_mismatches(db, org_id=s["org"]) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("check", reconcile.CHECKS)
async def test_each_kind_of_drift_is_reported(client, tenant, db, check):
    s = await _shop(client, tenant)
    if check == "account":
        await db.execute(update(Account).where(Account.id == s["till"]).values(balance=Account.balance + 5))
        want = s["till"]
    elif check == "client":
        await db.execute(update(Client).where(Client.id == s["client"]).values(balance=Client.balance - 7))
        want = s["client"]
    elif check == "invoice":
        await db.execute(update(Invoice).where(Invoice.id == s["invoice"]).values(amount_paid=100))
        want = s["invoice"]
    elif check == "split_leg":
        # a client advance whose cash leg was voided on its own (till reversed, credit kept)
        r = await client.post(f"/api/clients/{s['client']}/balance", headers=tenant,
                              json={"kind": "advance", "amount": 30, "account_id": s["till"]})
        assert r.status_code == 201, r.text
        want = (await db.execute(select(Transaction.id).where(
            Transaction.account_id == s["till"], Transaction.ref_type == "client"))).scalar_one()
        await db.execute(update(Transaction).where(Transaction.id == want).values(status="deleted"))
        await db.execute(update(Account).where(Account.id == s["till"]).values(balance=Account.balance - 30))
    else:
        await db.execute(update(StockLevel).where(StockLevel.part_id == s["part"])
                         .values(qty_available=StockLevel.qty_available + 2))
        want = f"{s['part']}@{s['loc']}"
    await db.commit()
    found = await reconcile.find_mismatches(db, org_id=s["org"])
    assert [(m.check, m.entity_id) for m in found] == [(check, want)], found
    assert found[0].diff != 0


@pytest.mark.asyncio
async def test_deleted_transactions_dont_count_and_corrected_ones_do(client, tenant, db):
    """H1's rule: a deleted transaction left the balance; a corrected one is still in it (its
    correction delta row carries the difference)."""
    s = await _shop(client, tenant)
    r = await client.post("/api/cash/transactions", headers=tenant, json={
        "account_id": s["till"], "txn_type": "income", "amount": 40, "comment": "float"})
    assert r.status_code in (200, 201), r.text
    tid = (await db.execute(select(Transaction.id).where(
        Transaction.account_id == s["till"], Transaction.comment == "float"))).scalar_one()
    r = await client.post(f"/api/cash/transactions/{tid}/correct", headers=tenant, json={"delta": 5, "reason": "typo"})
    assert r.status_code == 201, r.text
    assert await reconcile.find_mismatches(db, org_id=s["org"]) == []
    r = await client.delete(f"/api/cash/transactions/{tid}", headers=tenant)
    assert r.status_code in (200, 204), r.text
    db.expire_all()
    assert await reconcile.find_mismatches(db, org_id=s["org"]) == []
    st = (await db.execute(select(Transaction.status).where(Transaction.id == tid))).scalar()
    assert st in ("deleted", "corrected"), st


@pytest.mark.asyncio
async def test_one_shop_is_checked_without_another_shops_drift(client, tenant, db):
    s = await _shop(client, tenant)
    r = await client.post("/api/auth/register", json={"org_name": "Other", "name": "O",
                                                      "email": "other-recon@t.dev", "password": "pw-test-12345"})
    other = {"Authorization": f"Bearer {r.json()['access_token']}"}
    o = await _shop(client, other)
    await db.execute(update(Account).where(Account.id == o["till"]).values(balance=999))
    await db.commit()
    assert await reconcile.find_mismatches(db, org_id=s["org"]) == []
    every = await reconcile.find_mismatches(db)
    assert [(m.org_id, m.check) for m in every] == [(o["org"], "account")]
    loc_org = (await db.execute(select(Location.org_id).where(Location.id == s["loc"]))).scalar()
    assert loc_org == s["org"]


@pytest.mark.asyncio
async def test_sweep_logs_each_mismatch_and_counts_it(client, tenant, db, caplog):
    s = await _shop(client, tenant)
    await db.execute(update(Client).where(Client.id == s["client"]).values(balance=12345))
    await db.commit()
    with caplog.at_level(logging.ERROR, logger="tekly.reconcile"):
        found = await reconcile.sweep(db)
    assert len(found) == 1
    assert reconcile.LAST_COUNTS["client"] == 1 and reconcile.LAST_COUNTS["account"] == 0
    msgs = [r.getMessage() for r in caplog.records if r.name == "tekly.reconcile"]
    assert any("check=client" in m and s["client"] in m for m in msgs), msgs
    assert not any("Sweep" in m for m in msgs)          # ids and amounts only, never names
    from backend.core.observability import render_metrics
    assert 'repairos_integrity_mismatches{check="client"} 1' in render_metrics()


def test_the_sweep_is_scheduled_daily():
    import backend.core.scheduled  # noqa: F401  (registers the periodics)
    from backend.core.jobs import job_runner
    p = next((p for p in job_runner._periodics if p.name == "integrity-sweep"), None)
    assert p is not None, "integrity-sweep is not scheduled"
    assert p.interval_s == 24 * 3600


def test_the_sweep_waits_a_day_before_its_first_run():
    """A nightly full-database sweep must not run at every boot (each deploy and worker restart),
    nor in the background of tests that start the app: it waits one interval first."""
    import backend.core.scheduled  # noqa: F401
    from backend.core.jobs import job_runner
    p = next(p for p in job_runner._periodics if p.name == "integrity-sweep")
    assert p.delay_first is True


@pytest.mark.asyncio
async def test_a_delayed_periodic_does_not_run_at_start(monkeypatch):
    import asyncio

    from backend.core import jobs
    ran = []
    runner = jobs.JobRunner()

    @runner.every(3600, "probe-delayed", delay_first=True)
    async def delayed():
        ran.append("delayed")

    @runner.every(3600, "probe-now")
    async def now():
        ran.append("now")
    await runner.start()
    await asyncio.sleep(0.2)
    await runner.stop()
    assert ran == ["now"]
