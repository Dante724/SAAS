"""Build 144av (C2) — money that physically moves must hit a cashbox account, not just a
client/supplier balance. Three paths historically skipped the cashbox:
  * finance.record_payment (invoice pay) — posted a Payment + ledger, no Transaction
  * clients.move_balance (advance/payout) — moved the contact balance, no Transaction
  * stock_docs supplier return with settlement="cash" — accepted account_id, dropped it

Each now posts through post_transaction when a cash account is supplied, so the account
balance reflects reality. account_id stays optional → no change to callers that omit it.
"""
import pytest
from sqlalchemy import select


async def _account(client, h, name="Till"):
    await client.post("/api/cash/accounts", json={"name": name, "payment_option": "cash"}, headers=h)
    a = [x for x in (await client.get("/api/cash/accounts", headers=h)).json()["items"] if x["name"] == name][0]
    return a["id"]


async def _acct_balance(client, h, aid):
    return [x for x in (await client.get("/api/cash/accounts", headers=h)).json()["items"] if x["id"] == aid][0]["balance"] or 0.0


async def _me(client, h):
    return (await client.get("/api/auth/me", headers=h)).json()


@pytest.mark.asyncio
async def test_invoice_cash_payment_posts_to_cashbox(client, tenant):
    aid = await _account(client, tenant)
    before = await _acct_balance(client, tenant, aid)
    cid = (await client.post("/api/clients", json={"name": "C", "phone": "+1"}, headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid}, headers=tenant)).json()["id"]
    inv = (await client.post("/api/finance/invoices", json={"order_id": oid, "subtotal": 100}, headers=tenant)).json()
    r = await client.post(f"/api/finance/invoices/{inv['id']}/pay",
                          json={"method": "cash", "amount": 100, "account_id": aid}, headers=tenant)
    assert r.status_code == 200, r.text
    after = await _acct_balance(client, tenant, aid)
    assert round(after - before, 2) == 100.0, (before, after)


@pytest.mark.asyncio
async def test_client_advance_and_payout_move_the_cashbox(client, tenant):
    aid = await _account(client, tenant)
    cid = (await client.post("/api/clients", json={"name": "C", "phone": "+1"}, headers=tenant)).json()["id"]
    base = await _acct_balance(client, tenant, aid)
    # Advance: client hands us cash → account goes UP.
    r = await client.post(f"/api/clients/{cid}/balance",
                          json={"kind": "advance", "amount": 60, "account_id": aid}, headers=tenant)
    assert r.status_code == 201, r.text
    assert round(await _acct_balance(client, tenant, aid) - base, 2) == 60.0
    # Payout: we hand cash back → account goes DOWN.
    await client.post(f"/api/clients/{cid}/balance",
                      json={"kind": "payout", "amount": 20, "account_id": aid}, headers=tenant)
    assert round(await _acct_balance(client, tenant, aid) - base, 2) == 40.0


@pytest.mark.asyncio
async def test_supplier_cash_return_posts_to_cashbox(client, tenant, db):
    from backend.models import Supplier
    me = await _me(client, tenant)
    aid = await _account(client, tenant)
    base = await _acct_balance(client, tenant, aid)
    # a part with stock to return
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    p = (await client.post("/api/inventory/parts", json={"name": "Board", "sku": "B1"}, headers=tenant)).json()
    await client.post(f"/api/inventory/parts/{p['id']}/adjust",
                      json={"location_id": loc, "qty": 10, "movement_type": "receive"}, headers=tenant)
    w = (await client.get("/api/settings/warehouses?kind=product", headers=tenant)).json()["items"][0]
    sup = Supplier(org_id=me["org_id"], name="Acme", balance=0)
    db.add(sup); await db.commit(); await db.refresh(sup)

    r = await client.post("/api/stock-docs/returns", json={
        "warehouse_id": w["id"], "supplier_id": sup.id, "settlement": "cash", "account_id": aid,
        "lines": [{"part_id": p["id"], "qty": 2, "unit_cost": 5}]}, headers=tenant)
    assert r.status_code == 201, r.text
    # Supplier refunds us 10 in cash → account goes UP by 10.
    assert round(await _acct_balance(client, tenant, aid) - base, 2) == 10.0
