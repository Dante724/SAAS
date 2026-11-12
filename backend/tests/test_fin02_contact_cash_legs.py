"""Audit FIN-02 (2026-11-12): the cash leg of a client advance or payout, an invoice overpayment kept
as credit, or a supplier payment could be deleted (or corrected) on its own from Cash.

The till went back while the client kept the credit (and could spend it on a job), or the supplier
stayed paid in AP. Each side still matched its own ledger, so the nightly sweep saw nothing.

As for the document legs fixed under M3, Cash now refuses to delete or correct these legs and points
at the document side instead. Restoring a leg deleted before this fix is still allowed: only the
till moved then, so restoring it puts the two sides back together. The sweep now reports such a
split leg ("split_leg"), so existing desynced rows are found.
"""
from sqlalchemy import select, update

from backend.models import Account, Transaction
from backend.services import reconcile


async def _till(client, h, name="Till"):
    return (await client.post("/api/cash/accounts", json={"name": name, "payment_option": "cash"},
                              headers=h)).json()["id"]


async def _balance(client, h, aid):
    items = (await client.get("/api/cash/accounts", headers=h)).json()["items"]
    return [x for x in items if x["id"] == aid][0]["balance"] or 0.0


async def _org(client, h):
    return (await client.get("/api/auth/me", headers=h)).json()["org_id"]


async def _client_advance(client, h, till, amount=100):
    cid = (await client.post("/api/clients", json={"name": "Advance Client"}, headers=h)).json()["id"]
    r = await client.post(f"/api/clients/{cid}/balance", json={"kind": "advance", "amount": amount,
                                                               "account_id": till}, headers=h)
    assert r.status_code == 201, r.text
    return cid


async def _leg(db, till, ref_type):
    return (await db.execute(select(Transaction).where(
        Transaction.account_id == till, Transaction.ref_type == ref_type))).scalar_one()


async def test_client_advance_leg_cannot_be_deleted_or_corrected_alone(client, tenant, db):
    h = tenant
    till = await _till(client, h)
    cid = await _client_advance(client, h, till)
    leg = await _leg(db, till, "client")
    d = await client.delete(f"/api/cash/transactions/{leg.id}", headers=h)
    assert d.status_code == 409, d.text
    assert "balance" in d.json()["detail"].lower()
    c = await client.post(f"/api/cash/transactions/{leg.id}/correct", json={"delta": -50, "reason": "x"},
                          headers=h)
    assert c.status_code == 409, c.text
    assert await _balance(client, h, till) == 100.0
    bal = (await client.get(f"/api/clients/{cid}/balance", headers=h)).json()["balance"]
    assert bal == -100.0
    assert await reconcile.find_mismatches(db, org_id=await _org(client, h)) == []


async def test_supplier_payment_leg_cannot_be_deleted_alone(client, tenant, db):
    h = tenant
    till = await _till(client, h)
    await client.post("/api/cash/transactions", json={"account_id": till, "txn_type": "income", "amount": 500},
                      headers=h)
    s = await client.post("/api/suppliers", json={"name": "Parts Ltd"}, headers=h)
    assert s.status_code in (200, 201), s.text
    sid = s.json()["id"]
    p = await client.post(f"/api/suppliers/{sid}/pay", json={"amount": 120, "account_id": till}, headers=h)
    assert p.status_code == 200, p.text
    leg = await _leg(db, till, "supplier_payment")
    d = await client.delete(f"/api/cash/transactions/{leg.id}", headers=h)
    assert d.status_code == 409, d.text
    assert "supplier" in d.json()["detail"].lower()
    assert await _balance(client, h, till) == 380.0


async def test_sweep_reports_a_split_leg_and_restore_rejoins_it(client, tenant, db):
    """A leg deleted before this fix: only the till moved. The sweep names it, and restoring it
    (the till takes the money back, matching the credit the client still has) clears it."""
    h = tenant
    till = await _till(client, h)
    await _client_advance(client, h, till)
    leg = await _leg(db, till, "client")
    # what the old delete did: void the row and reverse the till, nothing else
    await db.execute(update(Transaction).where(Transaction.id == leg.id).values(status="deleted"))
    await db.execute(update(Account).where(Account.id == till).values(balance=Account.balance - 100))
    await db.commit()
    org = await _org(client, h)
    found = await reconcile.find_mismatches(db, org_id=org)
    assert [(m.check, m.entity_id) for m in found] == [("split_leg", leg.id)], found
    r = await client.post(f"/api/cash/transactions/{leg.id}/restore", headers=h)
    assert r.status_code == 200, r.text
    assert await _balance(client, h, till) == 100.0
    db.expire_all()
    assert await reconcile.find_mismatches(db, org_id=org) == []
